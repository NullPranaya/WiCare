"""Live loop for ESPectre boards (the team's WiCare setup): board motion score SSE -> fused level -> WiCare.
  python -m monitoring.espectre_worker                                    # boards from ESPECTRE_BOARDS in .env
  python -m monitoring.espectre_worker --boards b1=http://172.20.10.3:62587/espectre/v1,b2=http://172.20.10.4:62587/espectre/v1
  python -m monitoring.espectre_worker --replay data/sessions/20261114-190000.jsonl   # a recorded session (ours or WiCare's)
Live runs are recorded to data/sessions/<time>.jsonl (WiCare's format): the raw material for the fall dataset.
Each board streams `event: motion` / `data: {"timestamp_ms", "state", "score"}` about 4 times a second
(GET <endpoint>/events, Origin https://test.espectre.dev). With two or more boards, a burst only counts if every
board saw it (monitoring.burst_rule.Fusion). Each sample is posted to /ingest/observation with the fused level;
the backend runs the burst-then-stillness rule (DETECTOR=burst). The board allows at most two stream clients,
so do not run WiCare's own app against the same boards at the same time. Never opens the raw /csi stream."""
import argparse, json, os, queue, threading, time
import requests
from dotenv import load_dotenv
from monitoring.burst_rule import Fusion

ORIGIN = 'https://test.espectre.dev'
HEADERS = {'Origin': ORIGIN, 'Accept': 'text/event-stream', 'Cache-Control': 'no-store'}

def parse_boards(raw):
    out = []
    for entry in (raw or '').split(','):
        name, sep, url = entry.strip().partition('=')
        if entry.strip():
            if not sep or not url.strip().startswith('http'):
                raise SystemExit(f'bad board entry {entry!r}, expected id=http://<ip>:62587/espectre/v1')
            out.append((name.strip(), url.strip().rstrip('/')))
    if not out:
        raise SystemExit('no boards: set ESPECTRE_BOARDS in .env or pass --boards')
    return out

def sse_lines_to_events(lines):
    """Minimal SSE parser: yields (event, payload dict)."""
    event, data = 'message', []
    for raw in lines:
        line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
        line = line.rstrip('\r\n')
        if not line:
            if data:
                try:
                    payload = json.loads('\n'.join(data))
                    if isinstance(payload, dict): yield event, payload
                except json.JSONDecodeError:
                    pass
            event, data = 'message', []
        elif line.startswith(':'):
            continue
        else:
            name, _, value = line.partition(':')
            value = value[1:] if value.startswith(' ') else value
            if name == 'event': event = value
            elif name == 'data': data.append(value)

def board_reader(board, url, out):
    """One thread per board: puts ('sample', board, t, score) or ('status', board, connected, detail)."""
    backoff = (1, 2, 5); fails = 0
    while True:
        detail = 'stream closed'
        try:
            with requests.get(url + '/events', headers=HEADERS, stream=True, timeout=(5, 15)) as r:
                if r.status_code != 200:
                    detail = f'HTTP {r.status_code}'
                else:
                    fails = 0
                    out.put(('status', board, True, 'connected', None))
                    for ev, d in sse_lines_to_events(r.iter_lines(chunk_size=1)):
                        if ev != 'motion': continue
                        try: score = float(d['score'])
                        except (KeyError, TypeError, ValueError): continue
                        out.put(('sample', board, time.time(), score, int(d.get('timestamp_ms', 0) or 0)))
        except requests.RequestException as e:
            detail = type(e).__name__
        out.put(('status', board, False, detail, None))
        time.sleep(backoff[min(fails, len(backoff) - 1)]); fails += 1

def replay_reader(path, out):
    """WiCare session file (data/sessions/*.jsonl: t, board, score) replayed in real time."""
    rows = []
    for line in open(path):
        try:
            r = json.loads(line); rows.append((float(r['t']), str(r['board']), float(r['score'])))
        except (ValueError, KeyError, TypeError):
            continue
    if not rows: raise SystemExit(f'no samples in {path}')
    t0, wall0 = rows[0][0], time.time()
    for b in sorted({r[1] for r in rows}): out.put(('status', b, True, 'replay', None))
    for t, b, s in rows:
        wait = (t - t0) - (time.time() - wall0)          # real time only: the rule times stillness by arrival
        if wait > 0: time.sleep(wait)
        out.put(('sample', b, time.time(), s, 0))
    out.put(('done', None, None, None, None))

def post(api, sensor, score, level, source, threshold):
    try:
        r = requests.post(api + '/ingest/observation', timeout=5, json=dict(
            sensor_id=sensor, score=score, motion=level == 'motion', source=source, level=level)).json()
        if r.get('event'):
            e = r['event']; print('  EVENT', e['id'], e['event_type'], e.get('policy'))
    except requests.RequestException as e:
        print('  backend unreachable:', e)

def open_session(record_dir):
    """Same format as WiCare's app (one JSON line per board sample: t, board, score, device_ms), so recordings
    from either app can be replayed (--replay) and labelled with the same tools."""
    os.makedirs(record_dir, exist_ok=True)
    path = os.path.join(record_dir, time.strftime('%Y%m%d-%H%M%S') + '.jsonl')
    print('recording to', path)
    return open(path, 'a', buffering=1)

def run(boards, out, api, sensor, source, threshold, rec=None):
    names = [b for b, _ in boards]
    fusion = Fusion(names, threshold) if len(names) > 1 else None
    last_print = 0
    while True:
        kind, board, a, b, dev_ms = out.get()
        if kind == 'done': print('replay finished'); return
        if kind == 'status':
            print(f'{time.strftime("%H:%M:%S")} board {board}: {"connected" if a else "DISCONNECTED " + b}')
            if not a and fusion: fusion.disconnect(board)
            continue
        t, score = a, b
        if rec: rec.write(json.dumps(dict(t=round(t, 3), board=board, score=score, device_ms=dev_ms)) + '\n')
        if fusion:
            level = fusion.update(board, t, score)
            if level is None: continue                      # not every board has reported, or one went silent
            score = fusion.score()
        else:
            level = 'motion' if score >= threshold else 'still'
        if t - last_print >= 1:
            print(f'{time.strftime("%H:%M:%S")} score {score:.3f} {level.upper()}'); last_print = t
        post(api, sensor, round(score, 4), level, source, threshold)

if __name__ == '__main__':
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument('--boards', default=os.getenv('ESPECTRE_BOARDS', ''))
    ap.add_argument('--replay', help='replay a WiCare session .jsonl instead of live boards')
    ap.add_argument('--api', default='http://127.0.0.1:8000')
    ap.add_argument('--sensor', default=os.getenv('SENSOR_ID', 'esp32_01'))
    ap.add_argument('--threshold', type=float, default=float(os.getenv('BURST_THRESHOLD', '0.962')))
    ap.add_argument('--record-dir', default='data/sessions', help="where live sessions are saved ('' = off)")
    a = ap.parse_args()
    q = queue.Queue()
    if a.replay:
        boards = [(b, '') for b in sorted({json.loads(l)['board'] for l in open(a.replay) if l.strip()})]
        threading.Thread(target=replay_reader, args=(a.replay, q), daemon=True).start()
        source = 'espectre_replay'
    else:
        boards = parse_boards(a.boards)
        for b, url in boards:
            threading.Thread(target=board_reader, args=(b, url, q), daemon=True).start()
        source = 'espectre_live'
    print('boards:', ', '.join(f'{b} {u}' for b, u in boards))
    rec = open_session(a.record_dir) if a.record_dir and not a.replay else None
    run(boards, q, a.api, a.sensor, source, a.threshold, rec)
