"""Score-based temporal rules on ESPectre movement scores. Not a fall classifier.
Thresholds come from a short consented calibration (calibrate()), not from the demo test cases."""
from collections import deque
from dataclasses import dataclass, field
import numpy as np

@dataclass
class Thresholds:
    high: float        # score counted as an abrupt movement
    low: float         # score counted as low activity
    still_s: float = 10.0        # low activity after abrupt movement (demo value)
    inactive_s: float = 60.0     # unusual low activity in expected-active context (demo value)

def calibrate(quiet_scores, normal_scores, k=6.0):
    """Robust thresholds from short quiet + normal-motion sessions (median + k*MAD)."""
    q, n = np.asarray(quiet_scores, float), np.asarray(normal_scores, float)
    mad = lambda a: 1.4826 * np.median(np.abs(a - np.median(a))) + 1e-9
    low = float(np.median(q) + 3 * mad(q))
    high = float(max(np.percentile(n, 99), np.median(n) + k * mad(n)))
    if high <= low:
        raise ValueError('calibration did not separate quiet from motion; recheck sensor placement')
    return Thresholds(high=high, low=low)

@dataclass
class Detector:
    th: Thresholds
    expected_active: bool = True
    _abrupt: tuple | None = None          # (ts, obs_id, score)
    _low_since: float | None = None
    _inactive_fired: bool = False
    _recent: deque = field(default_factory=lambda: deque(maxlen=600))

    def reset(self):
        """Call on sensor gap/outage: never bridge a gap and call it inactivity."""
        self._abrupt, self._low_since, self._inactive_fired = None, None, False
        self._recent.clear()

    def feed(self, obs):
        """obs: dict(id, ts, score). Returns an event dict or None."""
        ts, s = obs['ts'], obs['score']
        self._recent.append(obs)
        if s >= self.th.high:
            self._abrupt, self._low_since, self._inactive_fired = (ts, obs['id'], s), None, False
            return None
        if s > self.th.low:
            self._low_since, self._inactive_fired = None, False
            if self._abrupt and ts - self._abrupt[0] > 5:      # movement resumed, not followed by stillness
                self._abrupt = None
            return None
        if self._low_since is None:
            self._low_since = ts
        still = ts - self._low_since
        if self._abrupt and still >= self.th.still_s:
            a_ts, a_id, a_s = self._abrupt
            self._abrupt = None
            ev = [o['id'] for o in self._recent if o['ts'] >= a_ts - 2]
            return dict(event_type='abrupt_motion_then_inactivity', start_ts=a_ts, evidence_ids=ev,
                        observations=dict(peak_score=round(a_s, 4), idle_seconds=round(still, 1),
                                          high_threshold=self.th.high, low_threshold=self.th.low))
        if self.expected_active and not self._inactive_fired and still >= self.th.inactive_s:
            self._inactive_fired = True
            ev = [o['id'] for o in self._recent if o['ts'] >= self._low_since][-50:]
            return dict(event_type='prolonged_low_activity', start_ts=self._low_since, evidence_ids=ev,
                        observations=dict(idle_seconds=round(still, 1), low_threshold=self.th.low))
        return None
