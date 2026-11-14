"""Live ESP32 CSI (64 subcarriers, 128 int8, non-HT 20 MHz) -> WiFall layout (52 amplitudes) -> 3 s windows.

ESP32 native LLTF order: buffer index k holds subcarrier k for k=0..31 and k-64 for k=32..63.
Nulls: DC (0) and guards (-32..-27, +27..+31) = 12. Data subcarriers: -26..-1, +1..+26.
WiFall's 52 look like frequency order [-26..-1, +1..+26]: neighbouring phase steps are smooth across
positions 25/26 (checked on WiFall). Confirm on live data with order_check() before trusting predictions."""
import numpy as np

SORTED = np.r_[38:64, 1:27]          # [-26..-1, +1..+26]  (default, matches WiFall evidence)
RAW_STRIPPED = np.r_[1:27, 38:64]    # [+1..+26, -26..-1]  (alternative to test)
POS_PLUS1 = {'sorted': 26, 'raw_stripped': 0}   # where subcarrier +1 lands

def to_complex64(buf):
    """128 int8 values, ESP32 stores (imag, real) per subcarrier. Amplitude doesn't care about the order."""
    a = np.asarray(buf, dtype=np.float32).reshape(64, 2)
    return a[:, 1] + 1j * a[:, 0]

def to_wifall(buf, first_word_invalid=False, layout='sorted'):
    """-> 52 amplitudes in WiFall order. Invalid first word zeroes native 0 (DC, dropped) and +1 (kept):
    replace +1 with its neighbour instead of feeding a fake zero."""
    c = to_complex64(buf)
    idx = SORTED if layout == 'sorted' else RAW_STRIPPED
    amp = np.abs(c[idx])
    if first_word_invalid:
        p = POS_PLUS1[layout]
        amp[p] = amp[p + 1]
    return amp.astype(np.float32)

def order_check(bufs):
    """Phase smoothness at the half boundary for each layout. Lower boundary/median ratio = adjacent in frequency.
    On WiFall: correct layout ~0.1, wrong layout ~9. Pick the layout with the much smaller ratio."""
    out = {}
    C = np.stack([to_complex64(b) for b in bufs])
    for name, idx in (('sorted', SORTED), ('raw_stripped', RAW_STRIPPED)):
        c = C[:, idx]
        step = np.abs(np.angle(np.exp(1j * np.angle(c[:, 1:] * np.conj(c[:, :-1]))).mean(0)))
        out[name] = round(float(step[25] / (np.median(step) + 1e-9)), 2)
    return out

def window(rx_times, seqs, amps, dt=0.02, length_s=2.96, max_gap_s=0.1, min_coverage=0.8, seq_mod=None):
    """Build one model window (T x 52 on a dt grid) from the most recent packets of ONE board.
    Uses laptop receive time (no chip timestamp); sequence numbers detect drops. Returns (x, info) or (None, info)."""
    t = np.asarray(rx_times, float); s = np.asarray(seqs, np.int64); A = np.asarray(amps, np.float32)
    keep = t >= t[-1] - length_s
    t, s, A = t[keep], s[keep], A[keep]
    ds = np.diff(s) % seq_mod if seq_mod else np.diff(s)
    dropped = int(np.clip(ds - 1, 0, None).sum())
    info = dict(n=len(t), dropped=dropped, coverage=round(len(t) / (length_s / dt), 2),
                max_gap_s=round(float(np.diff(t).max()), 3) if len(t) > 1 else None)
    if len(t) < 2 or info['coverage'] < min_coverage or info['max_gap_s'] > max_gap_s:
        return None, {**info, 'ok': False}
    T = int(round(length_s / dt))
    grid = t[-1] - length_s + np.arange(T) * dt
    x = np.stack([np.interp(grid, t, A[:, k]) for k in range(52)], 1)
    return x.astype(np.float32), {**info, 'ok': True}
