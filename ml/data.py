"""WiFall loading. Label = class folder, participant = ID folder. Each clip is ~3 s (~250 packets)."""
from pathlib import Path
import json, re
import numpy as np, pandas as pd

CLASSES = ('fall', 'jump', 'sit', 'stand', 'walk')
DT = 0.012          # measured median packet interval (~83 Hz)
T = 248             # fixed grid length, ~2.98 s
MAX_GAP = 0.10      # s; clips with a larger gap are rejected
MIN_DUR = 2.5       # s

def manifest(root):
    rows = []
    for p in sorted(Path(root).rglob('*.csv')):
        pid, cls = p.parts[-3], p.parts[-2].lower()
        if not re.fullmatch(r'ID\d+', pid) or cls not in CLASSES:
            print('skip', p); continue
        rows.append(dict(path=str(p), recording_id=f'{pid}/{cls}/{p.name}',
                         participant_id=pid, label=cls))
    if not rows:
        raise ValueError('no labeled files')
    return pd.DataFrame(rows)

def parse(s):
    a = np.array(s.strip('[]').split(','), dtype=np.float32)
    if a.size != 104 or not np.isfinite(a).all():
        raise ValueError('bad packet')
    return np.hypot(a[0::2], a[1::2])          # 52 amplitudes

def load_clip(path):
    """Return (T x 52 amplitude on a 12 ms grid, info dict) or raise."""
    df = pd.read_csv(path, usecols=['local_timestamp', 'data', 'taget'])
    amps, ts = [], []
    for t, s in zip(df.local_timestamp.values, df.data.values):
        try: amps.append(parse(s)); ts.append(int(t))
        except ValueError: pass
    if len(amps) < 10:
        raise ValueError('too few valid packets')
    ts = np.asarray(ts, dtype=np.int64)
    t = np.concatenate([[0], np.cumsum((np.diff(ts) % 2**32) / 1e6)])  # µs counter, uint32 wrap
    dur, gap = t[-1], np.diff(t).max()
    if dur < MIN_DUR: raise ValueError(f'short {dur:.2f}s')
    if gap > MAX_GAP: raise ValueError(f'gap {gap*1e3:.0f}ms')
    a = np.stack(amps)
    grid = np.arange(T) * DT
    x = np.stack([np.interp(grid, t, a[:, k]) for k in range(52)], 1)   # edge-holds past end
    return x.astype(np.float32), dict(n_valid=len(amps), n_rows=len(df), dur_s=float(dur),
                                      max_gap_ms=float(gap * 1e3), padded=bool(dur < grid[-1]))

def to_rate(X, rate_hz):
    """Resample clips from the 12 ms (83.3 Hz) grid to rate_hz with an anti-alias filter.
    50 Hz = the live boards' rate. Returns (X, dt)."""
    from fractions import Fraction
    from scipy.signal import resample_poly
    if rate_hz is None:
        return X, DT
    fr = Fraction(rate_hz * DT).limit_denominator(50)      # 50 Hz -> 3/5
    Y = resample_poly(X, fr.numerator, fr.denominator, axis=1)
    n = int(np.floor(X.shape[1] * rate_hz * DT))           # drop filter-padded tail sample
    return Y[:, :n].astype(np.float32), 1.0 / rate_hz

def build(root, out):
    m = manifest(root)
    X, keep, qual = [], [], []
    for i, r in m.iterrows():
        try:
            x, info = load_clip(r.path); X.append(x); keep.append(i)
            qual.append(dict(recording_id=r.recording_id, ok=True, **info))
        except ValueError as e:
            qual.append(dict(recording_id=r.recording_id, ok=False, reason=str(e)))
    m = m.loc[keep].reset_index(drop=True)
    np.savez_compressed(out, X=np.stack(X), y=m.label.map(CLASSES.index).values,
                        groups=m.participant_id.values, ids=m.recording_id.values)
    m.to_csv(Path(out).with_name('manifest.csv'), index=False)
    pd.DataFrame(qual).to_csv(Path(out).with_name('data_quality.csv'), index=False)
    print(f'kept {len(m)}/{len(qual)}'); print(m.groupby(['participant_id', 'label']).size().unstack())

if __name__ == '__main__':
    import sys; build(sys.argv[1], sys.argv[2])
