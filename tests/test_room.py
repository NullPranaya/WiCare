import numpy as np
from monitoring.policy import DEMO_SOURCES, LIVE_SOURCES
from sensors.csi_adapter import order_check_lag2, window, SORTED

def _bufs(n=300, seed=0, zero_plus1=True):
    """Synthetic L-LTF with a smooth phase ramp over frequency in native ESP32 order (0..31, -32..-1)."""
    rng = np.random.default_rng(seed)
    k = np.r_[0:32, -32:0]
    out = []
    for _ in range(n):
        c = 30 * np.exp(1j * (0.2 * k + rng.normal(0, 0.05, 64)))
        c[(np.abs(k) > 26) | (k == 0)] = 0
        if zero_plus1: c[1] = 0                      # first_word_sanitized zeroes subcarriers 0 and +1
        out.append(np.stack([c.imag, c.real], 1).round().astype(int).ravel().tolist())
    return out

def test_lag2_layout_check_picks_sorted_despite_zeroed_plus1():
    r = order_check_lag2(_bufs())
    assert r['sorted'] < 2 and r['raw_stripped'] > 2 * r['sorted']   # -1 -> +2 spans 3 bins vs 2: ~1.5 is correct

def test_window_uses_real_rate_and_rejects_big_gaps():
    t = np.arange(0, 6, 1 / 29); s = np.arange(len(t)); A = np.ones((len(t), 52))
    x, info = window(t, s, A, expected_hz=29, max_gap_s=0.3, min_coverage=0.6)
    assert x is not None and x.shape == (148, 52)
    x, info = window(t, s, A)                                  # old 50 Hz assumption rejects 29 Hz data
    assert x is None
    t2 = np.r_[t[:100], t[110:] + 0.5]
    x, info = window(t2, np.arange(len(t2)), np.ones((len(t2), 52)), expected_hz=29, max_gap_s=0.3, min_coverage=0.6, end_t=t2[110])
    assert x is None and info['max_gap_s'] > 0.3

def test_room_replay_is_demo_only():
    assert 'room_replay' in DEMO_SOURCES and 'room_replay' not in LIVE_SOURCES and 'live_csi' in LIVE_SOURCES
