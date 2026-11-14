"""ESPectre motion SSE reader with reconnect. Field names in the motion payload: verify against your
firmware with `curl -N -H 'Origin: https://espectre.dev' http://$ESP_IP:62587/espectre/v1/events`
and set SCORE_KEYS / MOTION_KEYS if they differ."""
import json, time, requests

SCORE_KEYS = ('movement', 'score', 'motion_score', 'value')
MOTION_KEYS = ('motion', 'state', 'is_motion', 'detected')

def normalize(p):
    score = next((p[k] for k in SCORE_KEYS if isinstance(p.get(k), (int, float))), None)
    m = next((p[k] for k in MOTION_KEYS if k in p), None)
    if isinstance(m, str): m = m.lower() in ('motion', 'true', '1', 'detected')
    return dict(score=None if score is None else float(score), motion=None if m is None else bool(m))

def motion_stream(ip, port=62587, origin='https://espectre.dev', on_disconnect=None):
    url = f'http://{ip}:{port}/espectre/v1/events'
    while True:
        try:
            with requests.get(url, headers={'Origin': origin}, stream=True, timeout=(5, 30)) as r:
                r.raise_for_status()
                event, data = None, []
                for line in r.iter_lines(chunk_size=1, decode_unicode=True):
                    if isinstance(line, bytes): line = line.decode('utf-8')
                    if not line:
                        if event == 'motion' and data:
                            p = json.loads('\n'.join(data))
                            yield {'raw': p, **normalize(p), 'received_at': time.time()}
                        event, data = None, []
                    elif line.startswith('event:'): event = line[6:].strip()
                    elif line.startswith('data:'): data.append(line[5:].strip())
        except (requests.RequestException, json.JSONDecodeError) as e:
            if on_disconnect: on_disconnect(str(e))
            time.sleep(2)
