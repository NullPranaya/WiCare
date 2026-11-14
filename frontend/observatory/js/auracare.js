/**
 * AuraCare layer on RuView's observatory scene.
 *
 * BACKEND source (preferred): polls AuraCare `GET /care/state` once a second. The care pill, the event strip,
 *   the movement chart and the iMessage conversation all come from stored rows. The simulated RuView cards are
 *   hidden and the 3D view is labelled as a visualisation, so nothing on screen is invented.
 *   Backend URL: ?api=http://host:8000 (default: same host, port 8000).
 * LIVE source (RuView WebSocket, no AuraCare backend): reads `data.auracare`
 *   { status, label, title, step 0..4, since_utc }.
 * DEMO source (nothing reachable): plays an older-adult story on RuView's simulated data. Badge stays "DEMO".
 */
import { PoseSystem } from './pose-system.js';
import { BreathRing, RoomTrail, dotChart, TONES } from './aura-viz.js';

const STORY = [
  { scenario: 'elderly_care', dur: 26 },
  { scenario: 'fall_event', dur: 34 },
];
// seconds after the fall pose appears (demo pacing only)
const T_CHECKIN = 4, T_REPLY = 10, T_CAREGIVER = 11;
const API = new URLSearchParams(location.search).get('api') || `http://${location.hostname || 'localhost'}:8000`;
const POLL_MS = 1000, BACKEND_LOST_MS = 4000;

const $ = (id) => document.getElementById(id);
const POSE_WORDS = { walking: 'Walking around the living room', standing: 'Standing', sitting: 'Sitting down',
                     lying: 'Lying down', falling: 'Sudden movement', fallen: 'On the floor, not moving' };
const hhmm = (d) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const hhmmss = (d) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const WHO = { 'out:patient': 'AuraCare to patient', 'out:caregiver': 'AuraCare to nurse',
              'in:patient': 'Patient', 'in:caregiver': 'Nurse' };

export class AuraCare {
  constructor(obs) {
    this.obs = obs;
    PoseSystem.frail = true;            // slower gait, slight stoop (pose-system.js)
    this._last = { mode: false };       // so the first failed poll doesn't restart the demo story (?scene=fall)
    this.status = 'ok';
    this.breath = new BreathRing($('aura-breath-c'));
    this.room = new RoomTrail($('aura-room-c'));
    this.care = null; this._okAt = 0; this._scene = null; this._eventId = null; this._msgKey = '';
    // ?scene=fall starts the demo story at the fall (handy on stage)
    const scene = new URLSearchParams(location.search).get('scene');
    this.i = scene === 'fall' ? 0 : -1;
    this._next();
    this._poll();
  }

  // set only by the poller, so the render loop and the poller can never disagree
  get backend() { return this._last.mode === true; }

  async _poll() {
    try {
      const r = await fetch(`${API}/care/state`, { signal: AbortSignal.timeout(2500) });
      if (r.ok) { this.care = await r.json(); this._okAt = Date.now(); }
    } catch { /* backend not running: demo or RuView live mode */ }
    this._applyMode(!!this.care && Date.now() - this._okAt < BACKEND_LOST_MS);
    if (this.backend) this._renderBackend(this.care);
    setTimeout(() => this._poll(), POLL_MS);
  }

  _applyMode(on) {
    if (on === this._last.mode) return;
    this._last.mode = on;
    document.body.classList.toggle('aura-backend', on);
    $('aura-motion').hidden = !on;
    $('aura-chat').hidden = !on;
    if (on) { this._setScene('elderly_care'); this._eventId = null; }
    else { this._last.key = null; this.i = -1; this._next(); }
    const lbl = $('data-source-label');
    if (lbl && on) lbl.textContent = '3D VIEW SIMULATED';
  }

  update(data, dt, source) {
    if (this.backend) return;                       // backend polling drives the care layer
    this._instruments(data || {}, dt);
    if (source === 'ws') return this._live(data && data.auracare);
    this.t += dt;
    if (this.t > STORY[this.i].dur) return this._next();
    const pose = data && data.persons && data.persons[0] && data.persons[0].pose;
    if (!this.ev && (pose === 'falling' || pose === 'fallen')) this.ev = { t0: this.t, at: new Date() };
    if (!this.ev) return this._render({ status: 'ok', label: 'Monitoring', step: 0 });
    const e = this.t - this.ev.t0;
    const step = e < T_CHECKIN ? 1 : e < T_REPLY ? 2 : e < T_CAREGIVER ? 3 : 4;
    this._render({
      status: step >= 3 ? 'help' : 'watch',
      label: step >= 3 ? 'Help requested' : step === 2 ? 'Check-in sent' : 'Possible fall',
      title: step >= 3 ? 'Patient replied NO · nurse alerted' : 'Possible fall · unconfirmed',
      step, since: this.ev.at, reply: step >= 3 ? 'Reply: NO' : 'Reply',
    });
  }

  // breath ring, room trail and the plain-words line, all from the same frame RuView draws the scene with
  _instruments(d, dt) {
    const tone = TONES[this.status] || TONES.ok;
    const bpm = d.vital_signs && d.vital_signs.breathing_rate_bpm;
    const io = this.breath.draw(dt, bpm, tone);
    this._text('aura-bpm', bpm > 0 ? String(Math.round(bpm)) : '--');
    this._text('aura-inout', io);
    const persons = d.persons || [];
    this.room.draw(dt, persons, tone);
    const pose = persons[0] && persons[0].pose;
    const n = d.estimated_persons != null ? d.estimated_persons : persons.length;
    this._text('aura-room-label', n ? `${n} ${n === 1 ? 'person' : 'people'} · trail of the last 20 s` : 'room empty');
    this._text('aura-sentence', !n ? 'Nobody in the room' : POSE_WORDS[pose] || 'Moving');
  }

  _next() {
    this.i = (this.i + 1) % STORY.length;
    this.t = 0;
    this.ev = null;
    this._setScene(STORY[this.i].scenario);
    this._render({ status: 'ok', label: 'Monitoring', step: 0 });
  }

  _setScene(name) {
    if (this._scene === name) return;
    this._scene = name;
    this.obs._demoData.setScenario(name);
  }

  _live(a) {
    if (!a) return this._render({ status: 'off', label: 'No care data', step: 0 });
    this._render({ status: a.status, label: a.label, title: a.title, step: a.step || 0,
                   since: a.since_utc ? new Date(a.since_utc) : null });
  }

  // ---------- backend mode ----------
  _renderBackend(c) {
    const ev = c.event;
    // 3D view: a visualisation of the event, switched by real events only
    if (ev && ev.id !== this._eventId) { this._eventId = ev.id; this._setScene('fall_event'); }
    if (!ev && this._eventId) { this._eventId = null; this._setScene('elderly_care'); }

    let reply = c.reply || 'Reply';
    const left = c.checkin && c.checkin.seconds_left;
    if (c.checkin && c.checkin.state === 'pending' && left != null) reply = `Reply · ${Math.max(0, Math.ceil(left))}s`;
    let title = c.title;
    if (ev && ev.source !== 'live_csi' && ev.source !== 'live_score') title = `${title} · ${ev.source.replace('_', ' ')}`;
    this._render({ status: c.status, label: c.label, title, step: c.step || 0,
                   since: c.since_utc ? new Date(c.since_utc) : null, reply, state: c.checkin && c.checkin.state });
    this._renderMotion(c);
    this._renderChat(c);
  }

  _renderMotion(c) {
    const s = c.score || {}, fresh = c.sensor && c.sensor.fresh;
    this._text('aura-score', s.latest == null ? '--' : `${Math.round(s.latest * 100)}%`);
    const st = $('aura-motion-state');
    const moving = s.latest != null && s.latest >= 0.5;
    if (st) {
      st.dataset.s = s.latest == null ? '' : moving ? 'moving' : 'still';
      st.textContent = s.latest == null ? 'NO DATA' : moving ? 'MOVING' : 'STILL';
    }
    this._text('aura-sensor', fresh ? 'sensor live' : `sensor ${c.sensor ? c.sensor.status.replace('_', ' ') : '--'}`);
    dotChart($('aura-spark'), s.series || [], s.high, s.low);
    this._text('aura-sentence', !fresh ? "Can't see the room right now" : c.status === 'help' ? 'Help requested'
      : c.event ? 'Sudden movement, then stillness' : s.latest == null ? '' : moving ? 'Moving around' : 'Quiet');
  }

  _renderChat(c) {
    const msgs = c.messages || [];
    this._text('aura-chat-meta', c.checkin ? c.checkin.state.replace('_', ' ').toUpperCase() : '');
    const key = JSON.stringify(msgs.map((m) => [m.ts_utc, m.status]));
    if (key === this._msgKey) return;
    this._msgKey = key;
    const body = $('aura-chat-body');
    if (!body) return;
    body.textContent = '';
    if (!msgs.length) {
      const p = document.createElement('div');
      p.className = 'aura-empty';
      p.textContent = 'No active check-in. AuraCare texts only when a burst of movement is followed by stillness.';
      return body.appendChild(p);
    }
    for (const m of msgs) {
      const wrap = document.createElement('div');
      const failed = m.status === 'delivery_failed';
      wrap.className = `aura-msg ${m.direction} ${m.role}${failed ? ' failed' : ''}`;
      const who = document.createElement('span');
      who.className = 'aura-who'; who.textContent = WHO[`${m.direction}:${m.role}`] || m.role;
      const b = document.createElement('div');
      b.className = 'aura-bubble'; b.textContent = m.text;
      const st = document.createElement('span');
      st.className = 'aura-status';
      st.textContent = `${hhmmss(new Date(m.ts_utc))}${m.direction === 'out' ? ' · ' + (failed ? 'not delivered' : m.status === 'mock_sent' ? 'mock' : 'sent') : ''}`;
      wrap.append(who, b, st);
      body.appendChild(wrap);
    }
    body.scrollTop = body.scrollHeight;
  }

  // ---------- shared pill + strip ----------
  _render(s) {
    const key = JSON.stringify([s.status, s.label, s.title, s.step, s.reply, s.state]);
    if (key === this._last.key) return;
    this._last.key = key;
    const pill = $('care-status');
    if (pill) pill.dataset.state = s.status;
    this.status = s.status;
    document.body.dataset.care = s.status;
    this._text('care-status-label', s.label);
    const strip = $('care-strip');
    if (!strip) return;
    strip.hidden = !s.step;
    if (!s.step) return;
    strip.dataset.state = s.status;
    this._text('care-strip-title', s.title || '');
    this._text('care-strip-time', s.since ? hhmm(s.since) : '');
    const ok = s.status === 'ok', help = s.status === 'help';
    for (let k = 1; k <= 4; k++) {
      const el = $(`cs-${k}`);
      if (!el) continue;
      let v = k < s.step ? 'done' : k === s.step ? 'now' : '';
      if (k === 3 && s.step >= 3) v = ok ? 'ok' : 'help';
      if (k === 4 && s.step === 4) v = help ? 'help' : ok ? 'ok' : 'now';
      el.dataset.s = v;
    }
    this._text('cs-3', s.reply || 'Reply');
  }

  _text(id, v) {
    if (this._last[id] === v) return;
    this._last[id] = v;
    const el = $(id);
    if (el) el.textContent = v;
  }
}
