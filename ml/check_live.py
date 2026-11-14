"""Compatibility check for a short live recording vs WiFall. Adapt load() to the RuView JSON format.
  python -m ml.check_live recording.json
Reports rate, drops, invalid-word count, subcarrier layout check, and per-window predictions."""
import json, sys
import numpy as np
from sensors.csi_adapter import to_wifall, order_check, window

def load(path):
    """EXPECTED per packet: {"board": 1, "seq": 123, "rx_time": 1731600000.123, "csi": [128 ints],
    "first_word_invalid": 0, "sig_mode": 0, "cwb": 0, "channel": 6, "rssi": -40}. Edit to match RuView."""
    rows = json.load(open(path))
    return rows if isinstance(rows, list) else rows['packets']

if __name__ == '__main__':
    P = load(sys.argv[1])
    for b in sorted({p['board'] for p in P}):
        q = sorted((p for p in P if p['board'] == b), key=lambda p: p['rx_time'])
        t = np.array([p['rx_time'] for p in q]); s = np.array([p['seq'] for p in q])
        bad = [p for p in q if len(p['csi']) != 128]
        print(f"\nboard {b}: {len(q)} pkts, {len(q) / (t[-1] - t[0]):.1f} Hz, dropped(seq) {int(np.clip(np.diff(s) - 1, 0, None).sum())}, "
              f"len!=128: {len(bad)}, invalid-word: {sum(p.get('first_word_invalid', 0) for p in q)}, "
              f"sig_mode {sorted({p.get('sig_mode') for p in q})}, cwb {sorted({p.get('cwb') for p in q})}")
        print('layout check (correct layout << 1, wrong ~9):', order_check([p['csi'] for p in q if len(p['csi']) == 128][:500]))
        A = [to_wifall(p['csi'], p.get('first_word_invalid', 0)) for p in q if len(p['csi']) == 128]
        try:
            from ml.inference import ActivityModel
            m = ActivityModel()
            for end in range(150, len(A), 50):
                x, info = window(t[:end], s[:end], A[:end])
                print(' window', end, info, m.scores(x) if x is not None else '')
        except FileNotFoundError:
            print('train first: python -m ml.train_rf data/clips.npz --rate 50')
