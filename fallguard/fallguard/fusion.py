"""Combine several boards into one level: a burst must be seen by every board."""

from __future__ import annotations

from collections.abc import Sequence

from fallguard.board import MotionSample
from fallguard.detector import Level


class Fusion:
    def __init__(self, boards: Sequence[str], threshold: float, window_s: float = 1.0) -> None:
        self.boards = tuple(boards)
        self.threshold = threshold
        self.window_s = window_s
        self._latest: dict[str, float] = {}
        self._last_above: dict[str, float] = {}

    def update(self, sample: MotionSample) -> Level | None:
        if sample.board not in self.boards:
            return None
        self._latest[sample.board] = sample.score
        if sample.score >= self.threshold:
            self._last_above[sample.board] = sample.t
        if len(self._latest) < len(self.boards):
            return None
        if all(self._latest[b] < self.threshold for b in self.boards):
            return "still"
        if all(b in self._last_above and sample.t - self._last_above[b] <= self.window_s for b in self.boards):
            return "motion"
        return "mixed"
