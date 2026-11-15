"""Live loop: hub raw CSI -> room movement score (1 Hz) -> /ingest/observation -> temporal rules -> check-in.
  python -m monitoring.csi_worker --hub ws://127.0.0.1:8770/v1/csi          # live boards via hub.py
  python -m monitoring.csi_worker --replay <dataset dir> --chunks p1_moving_1,p1_still_1 --lead 15   # recorded, ~30 s to alert
  python -m monitoring.csi_worker --synthetic                               # labelled demo fallback, no CSI
Score = P(moving) from ml/room_motion.py on the last 3 s of nodes 2 and 3, then a rolling median of 3.
Window end = newest packet time (hub clock), so laptop clocks need not agree. No valid window on both
nodes = nothing posted, so the backend marks the sensor stale instead of inferring stillness."""
import argparse, gzip, json, os, time
from collections import deque
import numpy as np, requests
from ml.room_motion import RoomMotion, NODES
from sensors.csi_adapter import to_wifall

KEEP_S, MEDIAN_K = 8.0, 3

class Buffers:
    def __init__(self):
        self.d = {n: deque() for n in NODES}

    def add(self, p):
        q = self.d.get(p['node_id'])
        if q is None or p.get('raw_len', len(p['raw'])) < 128: return
        q.append((p['ts_unix'], p['seq'], to_wifall(p['raw'][:128], p.get('first_word_sanitized', False))))
        while q and q[-1][0] - q[0][0] > KEEP_S: q.popleft()

    def latest(self):
        return max((q[-1][0] for q in self.d.values() if q), default=None)

    def arrays(self):
        return {n: dict(t=np.array([r[0] for r in q]), seq=np.array([r[1] for r in q], np.int64),
                        amp=np.stack([r[2] for r in q])) for n, q in self.d.items() if len(q) >= 10}

def hub_packets(url):
    import websocket
    while True:
        try:
            ws = websocket.create_connection(url, timeout=10)
            print('connected', url)
            while True:
                p = json.loads(ws.recv())
                if p.get('type', 'auracare.csi') == 'auracare.csi': yield p
        except Exception as e:
            print('hub disconnected:', e); time.sleep(2)

def replay_packets(root, chunks, lead=None):
    """lead: play only the last `lead` seconds of the first chunk (shorter demo)."""
    for i, c in enumerate(chunks):
        rows = [json.loads(l) for l in gzip.open(os.path.join(root, 'decoded', f'{c}.jsonl.gz'), 'rt')]
        rows.sort(key=lambda r: r['ts_unix'])
        if i == 0 and lead:
            rows = [r for r in rows if r['ts_unix'] >= rows[-1]['ts_unix'] - lead]
        print('replaying', c, len(rows), 'packets')
        shift, wall0 = time.time() - rows[0]['ts_unix'], time.time()
        for r in rows:
            wait = (r['ts_unix'] - rows[0]['ts_unix']) - (time.time() - wall0)
            if wait > 0: time.sleep(wait)
            yield {**r, 'ts_unix': r['ts_unix'] + shift}

def post(api, sensor, score, source):
    try:
        r = requests.post(api + '/ingest/observation', timeout=5,
                          json=dict(sensor_id=sensor, score=score, motion=score >= 0.5, source=source)).json()
        if r.get('event'): print('  EVENT', r['event']['id'], r['event']['event_type'], r['event'].get('policy'))
    except requests.RequestException as e:
        print('  backend unreachable:', e)

def run(packets, api, sensor, source):
    m, buf, recent, next_t = RoomMotion(), Buffers(), deque(maxlen=MEDIAN_K), None
    for p in packets:
        buf.add(p)
        t = buf.latest()
        if next_t is None: next_t = t + 3.0
        if t < next_t: continue
        next_t = t + 1.0
        s = m.score(buf.arrays(), t)
        if s is None:
            print(f'{time.strftime("%H:%M:%S")} no valid window on both nodes'); recent.clear(); continue
        recent.append(s); sm = float(np.median(recent))
        print(f'{time.strftime("%H:%M:%S")} score {s:.2f} smoothed {sm:.2f} {"MOVING" if sm >= 0.5 else "still"}')
        post(api, sensor, round(sm, 4), source)

def synthetic(api, sensor):
    """Clearly labelled demo fallback: 2 s strong movement then 12 s still, source='synthetic'."""
    for s in [0.3] * 3 + [1.2] * 2 + [0.05] * 12:      # burst clears both detectors (0.8 score rule, 0.962 board rule)
        print('synthetic score', s); post(api, sensor, s, 'synthetic'); time.sleep(1)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--hub'); g.add_argument('--replay'); g.add_argument('--synthetic', action='store_true')
    ap.add_argument('--chunks', default='p1_moving_1,p1_still_1')
    ap.add_argument('--lead', type=float, default=None, help='replay: seconds of the first chunk to play (demo: 15)')
    ap.add_argument('--api', default='http://127.0.0.1:8000')
    ap.add_argument('--sensor', default=os.getenv('SENSOR_ID', 'esp32_01'))
    a = ap.parse_args()
    if a.synthetic: synthetic(a.api, a.sensor)
    elif a.hub: run(hub_packets(a.hub), a.api, a.sensor, 'live_csi')
    else: run(replay_packets(a.replay, a.chunks.split(','), a.lead), a.api, a.sensor, 'room_replay')
