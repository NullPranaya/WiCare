"""Live loop: ESPectre SSE -> /ingest/observation. Usage:
  python -m monitoring.runner 192.168.1.50            (real board)
  python -m monitoring.runner 127.0.0.1 --source wifall_replay   (replay sensor)"""
import argparse, requests
from sensors.espectre_client import motion_stream

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ip'); ap.add_argument('--sensor', default='esp32_01')
    ap.add_argument('--source', default='live_score'); ap.add_argument('--api', default='http://127.0.0.1:8000')
    a = ap.parse_args()
    first = True
    for m in motion_stream(a.ip, on_disconnect=lambda e: print('sensor disconnected:', e)):
        if first: print('first raw payload (check field names):', m['raw']); first = False
        if m['score'] is None:
            continue
        r = requests.post(a.api + '/ingest/observation', timeout=5,
                          json=dict(sensor_id=a.sensor, score=m['score'], motion=m['motion'], source=a.source)).json()
        if r.get('event'): print('EVENT', r['event']['id'], r['event']['event_type'], r['event']['policy'])

if __name__ == '__main__':
    main()
