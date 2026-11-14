"""Short consented calibration from the live stream: 20 s still room, 20 s normal movement.
  python -m monitoring.calibrate 192.168.1.50
Writes calibration.json (thresholds used by the detector). Demo calibration, not a personal routine."""
import argparse, json, time
from datetime import datetime, timezone
from sensors.espectre_client import motion_stream
from monitoring.anomaly import calibrate

def collect(ip, seconds, label):
    input(f'\n{label}: press Enter to start {seconds} s capture...')
    out, t0 = [], time.time()
    for m in motion_stream(ip):
        if m['score'] is not None: out.append(m['score'])
        if time.time() - t0 >= seconds: break
    print(f'{len(out)} samples, {len(out) / seconds:.1f}/s')
    return out

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('ip'); ap.add_argument('--seconds', type=int, default=20)
    a = ap.parse_args()
    q = collect(a.ip, a.seconds, 'QUIET (room still, person seated or absent)')
    n = collect(a.ip, a.seconds, 'NORMAL MOVEMENT (walk around normally, no falls)')
    th = calibrate(q, n)
    json.dump(dict(thresholds=th.__dict__, note=f'{a.seconds}s quiet + {a.seconds}s normal, live sensor',
                   created_utc=datetime.now(timezone.utc).isoformat(), n_quiet=len(q), n_normal=len(n)),
              open('calibration.json', 'w'), indent=2)
    print('calibration.json:', th)
