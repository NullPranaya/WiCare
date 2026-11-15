"""Ward demo: 5 rooms, each with a stress monitor (0-100 demo index, not a clinical measure).

The other rooms drift at calm levels (simulated). The live room (WARD_LIVE_ROOM, default 1) is set by a teammate
from the control page (GET /ward/control on a phone). When it stays high, AuraCare asks the nurse over iMessage:
"Do you want to check?" Reply 1 / YES -> the ward screen opens that room (3D, the only time anyone sees inside a
room). 2 / NO -> dismissed. Privacy: the ward overview shows closed rooms only.

Where the live room's stress comes from (WiCare hook):
  - 'board': the team's WiCare / ESPectre boards. Every board score that reaches /ingest/observation from
    `python -m monitoring.espectre_worker` is also fed here; stress = score / BURST_THRESHOLD * 70, so the board's own
    "strong movement" line (0.962) sits exactly on the alert line (70). Used while boards send data (< 3 s old).
  - 'remote': the control page. Moving it overrides the board for WARD_REMOTE_HOLD_S (30 s), and it is the source
    whenever no board is connected.

alert state: calm -> asked -> viewing | dismissed. A new alert needs the room to drop below LOW first (re-arm).
"""
import math, os, random, threading
from backend.db import new_id, now, iso

ROOMS = int(os.getenv('WARD_ROOMS', '1'))              # demo: one private room (set 5 for the ward view)
LIVE_ROOM = min(ROOMS, int(os.getenv('WARD_LIVE_ROOM', '1')))
HIGH, LOW, HOLD_S = 70.0, 50.0, 3.0      # alert when >= HIGH for HOLD_S; re-arm below LOW
BOARD_HOLD_S = 1.0                       # board bursts are short (WiCare: <= 3 s), so react after 1 s
BOARD_FRESH_S = 3.0                      # board data older than this = no board
REMOTE_HOLD_S = float(os.getenv('WARD_REMOTE_HOLD_S', '30'))
SERIES_N = 90                            # 1 Hz samples kept per room

ASK = ("\U0001FA7A AuraCare | Ward Alert\n\n"
       "Room {room}'s stress level is high ({value}).\n\n"
       "Do you want to check on them?\n\n"
       "Reply 1: YES\nReply 2: NO")
OPENED = "\U0001F49A AuraCare\n\nOpening Room {room} on the ward screen now."
DISMISSED = "\U0001FA7A AuraCare\n\nOkay, we'll keep watching Room {room}."


class Ward:
    def __init__(self, photon, db, board_threshold=0.962):
        self.photon, self.db = photon, db
        self.board_threshold = board_threshold
        self._board_peak, self._board_ts, self._remote_at = None, None, None
        self.lock = threading.RLock()
        self._seed = [random.random() * 100 for _ in range(5)]
        self.target = 30.0                      # live room, set by the control page
        self.value = 30.0
        self.series = {r: [] for r in range(1, ROOMS + 1)}
        self.alert = None                       # dict(id, room, state, since, value)
        self.armed = True
        self._high_since = None

    # ---- values
    def _calm(self, room, t):
        """Rooms 1-4: smooth random drift in the calm band (simulated)."""
        s = self._seed[room - 1]
        v = 32 + 7 * math.sin(t / 9 + s) + 4 * math.sin(t / 3.7 + s * 2) + 2 * math.sin(t * 1.3 + s)
        return round(max(5, min(60, v)), 1)

    def current(self, room, t=None):
        t = now() if t is None else t
        if room == LIVE_ROOM:
            return round(max(0, min(100, self.value + 1.5 * math.sin(t * 1.7))), 1)
        return self._calm(room, t)

    def set_target(self, value, t=None):
        """Remote (control page). Overrides the board for REMOTE_HOLD_S."""
        with self.lock:
            self.target = max(0.0, min(100.0, float(value)))
            self._remote_at = now() if t is None else t
        return self.target

    def board_sample(self, score, ts=None):
        """One WiCare board score (fused across boards by the worker). Keeps the peak between ticks so a short
        burst is never missed."""
        with self.lock:
            self._board_peak = score if self._board_peak is None else max(self._board_peak, score)
            self._board_ts = now() if ts is None else ts

    def source(self, t=None):
        t = now() if t is None else t
        if self._remote_at is not None and t - self._remote_at < REMOTE_HOLD_S:
            return 'remote'
        if self._board_ts is not None and t - self._board_ts < BOARD_FRESH_S:
            return 'board'
        return 'remote'

    # ---- 1 Hz tick: smooth the live room, record series, run the alert rule
    def tick(self, t=None):
        t = now() if t is None else t
        with self.lock:
            src = self.source(t)
            if src == 'board':
                if self._board_peak is not None:
                    self.value = max(0.0, min(100.0, self._board_peak / self.board_threshold * HIGH))
                    self._board_peak = None
            else:
                self.value += (self.target - self.value) * 0.5
            for r in range(1, ROOMS + 1):
                s = self.series[r]
                s.append((t, self.current(r, t)))
                del s[:-SERIES_N]
            v = self.current(LIVE_ROOM, t)
            if v < LOW:
                self.armed, self._high_since = True, None
                if self.alert and self.alert['state'] == 'dismissed':
                    self.alert = None
            elif v >= HIGH and self.armed and not self.alert:
                self._high_since = self._high_since or t
                if t - self._high_since >= (BOARD_HOLD_S if src == 'board' else HOLD_S):
                    self._open(LIVE_ROOM, v, t)

    def _open(self, room, value, t):
        aid = new_id('ward')
        self.alert = dict(id=aid, room=room, state='asked', since=t, value=round(value))
        self.armed, self._high_since = False, None
        self.photon.send('caregiver', ASK.format(room=room, value=round(value)), f'{aid}:ask', event_id=aid)

    # ---- nurse replies (from the Photon webhook) and screen buttons
    def waiting(self):
        return self.alert if self.alert and self.alert['state'] == 'asked' else None

    def reply(self, intent, source='imessage'):
        """intent 'acknowledged' (YES / 1) opens the room; 'help_requested' (NO / 2) dismisses it."""
        with self.lock:
            a = self.waiting()
            if not a:
                return None
            if intent == 'acknowledged':
                a.update(state='viewing', since=now(), via=source)
                if source == 'imessage':
                    self.photon.send('caregiver', OPENED.format(room=a['room']), f"{a['id']}:opened", event_id=a['id'])
            elif intent == 'help_requested':
                a.update(state='dismissed', since=now(), via=source)
                if source == 'imessage':
                    self.photon.send('caregiver', DISMISSED.format(room=a['room']), f"{a['id']}:dismissed", event_id=a['id'])
            return dict(a)

    def view(self, room=LIVE_ROOM):
        """Screen button: open a room without waiting for the phone (fallback if iMessage is slow)."""
        with self.lock:
            if self.waiting():
                return self.reply('acknowledged', source='screen')
            self.alert = dict(id=new_id('ward'), room=room, state='viewing', since=now(), value=round(self.current(room)), via='screen')
            self.armed = False
            return dict(self.alert)

    def close(self):
        """Back to the ward overview."""
        with self.lock:
            self.alert = None
            self.armed = self.current(LIVE_ROOM) < LOW
            self._high_since = None

    # ---- snapshot for the ward UI
    def state(self):
        with self.lock:
            t = now()
            rooms = [dict(room=r, value=self.current(r, t),
                          series=[round(v, 1) for _, v in self.series[r]],
                          source=self.source(t) if r == LIVE_ROOM else 'simulated',
                          level='high' if self.current(r, t) >= HIGH else 'elevated' if self.current(r, t) >= LOW else 'calm')
                     for r in range(1, ROOMS + 1)]
            a = dict(self.alert) if self.alert else None
        msgs = []
        if a:
            a['since_utc'] = iso(a['since'])
            msgs = [dict(direction=m['direction'], role=m['role'], text=m['text'], status=m['status'],
                         ts_utc=iso(m['created_ts']))
                    for m in self.db.q('SELECT * FROM messages WHERE event_id=? ORDER BY created_ts', (a['id'],))]
        return dict(rooms=rooms, alert=a, messages=msgs, high=HIGH, low=LOW, target=round(self.target),
                    live_room=LIVE_ROOM, server_utc=iso(t))


CONTROL_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Room control</title>
<style>
:root{color-scheme:light dark;--bg:#f4f7f8;--card:#fff;--ink:#14232b;--mut:#5d7280;--acc:#0f8b8d;--hi:#d64545}
@media (prefers-color-scheme:dark){:root{--bg:#0f1518;--card:#182226;--ink:#e6eef1;--mut:#93a6b0}}
*{box-sizing:border-box}body{margin:0;font:16px system-ui,sans-serif;background:var(--bg);color:var(--ink);padding:16px}
.card{background:var(--card);border-radius:16px;padding:20px;max-width:440px;margin:0 auto}
h1{font-size:18px;margin:0 0 4px}p{color:var(--mut);margin:0 0 16px;font-size:14px}
.v{font-size:64px;font-weight:700;font-variant-numeric:tabular-nums}.v.hi{color:var(--hi)}
input{width:100%;height:44px;accent-color:var(--acc)}
.row{display:flex;gap:10px;margin-top:14px}button{flex:1;padding:16px;border:0;border-radius:12px;font-size:16px;
font-weight:600;color:#fff;background:var(--acc)}button.hi{background:var(--hi)}small{color:var(--mut)}
</style></head><body><div class="card">
<h1 id="h">Room stress monitor</h1><p>Drag or tap. 70+ for 3 s texts the nurse. While the WiCare board is connected it drives the monitor; moving this takes over for 30 s.</p>
<div class="v" id="v">--</div><input id="s" type="range" min="0" max="100" value="30">
<div class="row"><button id="calm">Calm (30)</button><button class="hi" id="high">Stressed (85)</button></div>
<p style="margin-top:14px"><small id="st">connecting...</small></p></div>
<script>
const s=document.getElementById('s'),v=document.getElementById('v'),st=document.getElementById('st');
async function set(x){s.value=x;try{await fetch('/ward/stress',{method:'POST',headers:{'content-type':'application/json'},
body:JSON.stringify({value:+x})})}catch(e){st.textContent='not connected'}}
s.oninput=()=>set(s.value);document.getElementById('calm').onclick=()=>set(30);document.getElementById('high').onclick=()=>set(85);
let first=true;async function poll(){try{const r=await (await fetch('/ward/state')).json();const room=r.rooms[r.live_room-1];if(first){s.value=r.target;first=false;document.getElementById('h').textContent='Room '+r.live_room+' stress monitor'}
v.textContent=Math.round(room.value);v.className='v'+(room.value>=r.high?' hi':'');
st.textContent=(room.source==='board'?'Source: WiCare board · ':'Source: this remote · ')+(r.alert?('nurse: '+r.alert.state):'no alert');}catch(e){st.textContent='not connected'}
setTimeout(poll,700)}poll();
</script></body></html>"""
