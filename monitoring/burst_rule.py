"""Burst-then-stillness fall rule for ESPectre boards (each board streams its own motion score ~4/s).
Adapted from the team's WiCare / FallGuard app (fallguard/detector.py and fusion.py): the rule and its numbers
were tuned on the real board in the demo room.

  MONITORING --score >= threshold--> BURST --score < threshold--> STILLNESS --still_s--> FALL (event)
  BURST longer than burst_max_s = walking / getting up -> MOVEMENT (no event), back to MONITORING once quiet.
  Motion again within burst_merge_s of a dip = the same burst. Gap > max_gap_s = NO_SIGNAL (never inferred as still).

`BurstDetector` has the same interface as monitoring.anomaly.Detector (feed(obs) -> event dict | None, reset()),
so the backend can use either. `Fusion` combines several boards: a burst counts only if every board saw it."""
from collections import deque
from dataclasses import dataclass

MONITORING, BURST, STILLNESS, MOVEMENT, NO_SIGNAL = 'MONITORING', 'BURST', 'STILLNESS', 'MOVEMENT', 'NO_SIGNAL'

@dataclass(frozen=True)
class BurstRule:
    threshold: float = 0.962     # raw board score counted as strong movement (calibrated in the room)
    burst_max_s: float = 3.0     # longer bursts are walking / getting up, not a fall
    still_s: float = 8.0         # stillness needed after the burst
    burst_merge_s: float = 0.75  # a brief dip inside a burst does not end it
    quiet_reset_s: float = 1.0   # quiet time that ends a MOVEMENT episode
    max_gap_s: float = 1.5       # longer silence = no signal

def level_for(score, threshold):
    return 'motion' if score >= threshold else 'still'

class BurstDetector:
    def __init__(self, rule: BurstRule):
        self.rule = rule
        self._recent = deque(maxlen=400)
        self.reset()

    def reset(self):
        self.state, self._last_t = NO_SIGNAL, None
        self._burst_start = self._still_start = self._quiet_start = None
        self._peak = 0.0

    def feed(self, obs):
        """obs: dict(id, ts, score[, level]). level ('motion'/'still'/'mixed') comes from multi-board fusion;
        without it the score is compared to the threshold. Returns an event dict when a fall pattern completes."""
        t, r = obs['ts'], self.rule
        if self._last_t is not None and t - self._last_t > r.max_gap_s:
            self.reset()                                   # silence: start over, never call it stillness
        self._last_t = t
        self._recent.append(obs)
        level = obs.get('level') or level_for(obs['score'], r.threshold)
        if self.state == NO_SIGNAL:
            self.state = MONITORING
        if self.state == MONITORING:
            if level == 'motion':
                self.state, self._burst_start, self._peak = BURST, t, obs['score']
        elif self.state == BURST:
            self._peak = max(self._peak, obs['score'])
            if level == 'still':
                self.state, self._still_start, self._burst_end = STILLNESS, t, t
            elif t - self._burst_start > r.burst_max_s:
                self._movement(t)
        elif self.state == STILLNESS:
            elapsed = t - self._still_start
            if level == 'motion' and elapsed <= r.burst_merge_s:
                self.state = BURST                          # brief dip: the same burst continues
                self._peak = max(self._peak, obs['score'])
                if t - self._burst_start > r.burst_max_s:
                    self._movement(t)
            elif level != 'still':
                self._movement(t)
            elif elapsed >= r.still_s:
                return self._fall(t)
        elif self.state == MOVEMENT:
            if level == 'still':
                if self._quiet_start is None:
                    self._quiet_start = t
                elif t - self._quiet_start >= r.quiet_reset_s:
                    self.state = MONITORING
            else:
                self._quiet_start = None
        return None

    def _movement(self, t):
        self.state, self._quiet_start = MOVEMENT, None

    def _fall(self, t):
        r, start = self.rule, self._burst_start
        burst_s, still_s = round(self._burst_end - start, 2), round(t - self._still_start, 1)
        ev = [o['id'] for o in self._recent if o['ts'] >= start - 2]
        self.state = MONITORING                           # one event per fall; the open check-in blocks repeats
        return dict(event_type='burst_then_stillness', start_ts=start, evidence_ids=ev,
                    observations=dict(peak_score=round(self._peak, 4), burst_seconds=burst_s, idle_seconds=still_s,
                                      high_threshold=r.threshold, low_threshold=r.threshold,
                                      burst_max_s=r.burst_max_s, still_required_s=r.still_s))

class Fusion:
    """Several boards -> one level. 'still' only if every board is below the threshold; 'motion' only if every
    board crossed it within window_s; otherwise 'mixed'. None until every board has reported, or if one went silent."""
    def __init__(self, boards, threshold, window_s=1.0, max_gap_s=1.5):
        self.boards, self.threshold, self.window_s, self.max_gap_s = tuple(boards), threshold, window_s, max_gap_s
        self.latest, self.last_t, self.last_above = {}, {}, {}

    def disconnect(self, board):
        self.latest.pop(board, None); self.last_t.pop(board, None)

    def update(self, board, t, score):
        if board not in self.boards: return None
        self.latest[board], self.last_t[board] = score, t
        if score >= self.threshold: self.last_above[board] = t
        if len(self.latest) < len(self.boards): return None
        if any(t - self.last_t[b] > self.max_gap_s for b in self.boards): return None
        if all(self.latest[b] < self.threshold for b in self.boards): return 'still'
        if all(b in self.last_above and t - self.last_above[b] <= self.window_s for b in self.boards): return 'motion'
        return 'mixed'

    def score(self):
        return max(self.latest.values()) if self.latest else None
