"""Combine several boards into one level: a burst must be seen by every board."""

from __future__ import annotations

from collections.abc import Sequence

from fallguard.board import MotionSample
from fallguard.detector import Level


class Fusion:
    def __init__(
        self, boards: Sequence[str], threshold: float, window_s: float = 1.0, max_gap_s: float = 1.5
    ) -> None:
        self.boards = tuple(boards)
        self.threshold = threshold
        self.window_s = window_s
        self.max_gap_s = max_gap_s
        self._latest: dict[str, float] = {}
        self._last_t: dict[str, float] = {}
        self._last_above: dict[str, float] = {}

    def disconnect(self, board: str) -> None:
        """Forget a board until it reports again, so its last score cannot count as still."""
        self._latest.pop(board, None)
        self._last_t.pop(board, None)

    def update(self, sample: MotionSample) -> Level | None:
        if sample.board not in self.boards:
            return None
        self._latest[sample.board] = sample.score
        self._last_t[sample.board] = sample.t
        if sample.score >= self.threshold:
            self._last_above[sample.board] = sample.t
        if len(self._latest) < len(self.boards):
            return None
        if any(sample.t - self._last_t[b] > self.max_gap_s for b in self.boards):
            return None  # a silent board: no level, so the detector's gap check takes over
        if all(self._latest[b] < self.threshold for b in self.boards):
            return "still"
        if all(b in self._last_above and sample.t - self._last_above[b] <= self.window_s for b in self.boards):
            return "motion"
        return "mixed"
