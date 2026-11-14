import numpy as np

def robust_z(x):
    med = np.median(x, 0, keepdims=True)
    mad = np.median(np.abs(x - med), 0, keepdims=True)
    return np.clip((x - med) / (1.4826 * mad + 1e-3), -10, 10)

def clip_features(x, dt=0.012):
    """Motion-dynamics features for one T x 52 clip. No absolute amplitude level (session leakage)."""
    xc = x - x.mean(0, keepdims=True)                 # remove static per-subcarrier level
    d = np.abs(np.diff(x, axis=0))
    turb = x.std(1)                                   # spread across subcarriers per packet
    energy = d.mean(1)                                # motion energy per step
    thirds = np.array_split(energy, 3)
    sub_std = xc.std(0)
    # PCA-1 of centred clip: dominant motion component
    u, s, vt = np.linalg.svd(xc, full_matrices=False)
    pc = u[:, 0] * s[0]
    spec = np.abs(np.fft.rfft(pc)) ** 2
    f = np.fft.rfftfreq(len(pc), d=dt)
    bands = [spec[(f >= lo) & (f < hi)].sum() for lo, hi in ((0.3, 1), (1, 2), (2, 4), (4, 8), (8, 20))]
    bands = np.log1p(np.asarray(bands) / (spec[1:].sum() + 1e-9) * 100)
    z = robust_z(x)
    return np.concatenate([
        [sub_std.mean(), sub_std.max(), np.median(sub_std),
         d.mean(), d.max(), np.percentile(d, 95),
         turb.std(), np.ptp(turb), np.abs(np.diff(turb)).mean(),
         energy.max() / (energy.mean() + 1e-6), np.argmax(energy) / len(energy),
         *[t.mean() for t in thirds],
         thirds[2].mean() / (thirds[0].mean() + 1e-6),
         s[0] ** 2 / (s ** 2).sum(), np.abs(z).mean(), (np.abs(z) > 3).mean()],
        bands]).astype(np.float32)

NAMES = ['sub_std_mean', 'sub_std_max', 'sub_std_med', 'd_mean', 'd_max', 'd_p95',
         'turb_std', 'turb_ptp', 'turb_dmean', 'energy_peak_ratio', 'energy_peak_pos',
         'energy_t1', 'energy_t2', 'energy_t3', 'energy_t3_over_t1',
         'pc1_var_frac', 'absz_mean', 'z_gt3_frac',
         'band_0.3_1', 'band_1_2', 'band_2_4', 'band_4_8', 'band_8_20']
