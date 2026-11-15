"""Replay a recorded session file (data/sessions/*.jsonl) as a live sample source."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fallguard.board import BoardStatus, MotionSample


def _rows(path: Path):
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
                yield float(row["t"]), str(row["board"]), float(row["score"]), int(row.get("device_ms", 0))
            except (ValueError, KeyError, TypeError):
                continue


class ReplaySource:
    def __init__(
        self,
        path: Path,
        *,
        speed: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.path = Path(path)
        self.speed = speed
        self.sleep = sleep
        self.clock = clock

    async def _iterate(self) -> AsyncIterator[MotionSample | BoardStatus]:
        previous_t: float | None = None
        board = None
        for t, board, score, device_ms in _rows(self.path):
            if previous_t is None:
                yield BoardStatus(board, True, "replay")
            else:
                await self.sleep(max(t - previous_t, 0.0) / self.speed)
            previous_t = t
            yield MotionSample(board, self.clock(), score, device_ms)
        if board is not None:
            yield BoardStatus(board, False, "replay finished")

    def __call__(self) -> AsyncIterator[MotionSample | BoardStatus]:
        return self._iterate()
