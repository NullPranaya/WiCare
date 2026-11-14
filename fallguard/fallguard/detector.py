"""Fall rule: a short burst of strong movement followed by sustained stillness.

Pure state machine. Callers pass host time in seconds; no I/O and no clocks here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from fallguard.config import RuleConfig

Level = Literal["motion", "still", "mixed"]


def level_for(score: float, threshold: float) -> Level:
    return "motion" if score >= threshold else "still"


class State(StrEnum):
    MONITORING = "MONITORING"
    BURST = "BURST"
    STILLNESS = "STILLNESS"
    MOVEMENT = "MOVEMENT"
    FALL = "FALL"
    NO_SIGNAL = "NO_SIGNAL"


@dataclass(frozen=True)
class Status:
    state: State
    since: float
    burst_s: float | None
    still_s: float | None
    armed: bool
    new_fall: bool = False


class FallDetector:
    def __init__(self, config: RuleConfig, armed: bool = True) -> None:
        self.config = config
        self.armed = armed
        self._state = State.MONITORING
        self._since = 0.0
        self._last_sample: float | None = None
        self._burst_start = 0.0
        self._last_motion = 0.0
        self._still_start = 0.0
        self._quiet_start: float | None = None
        self._burst_s: float | None = None
        self._still_s: float | None = None
        self._status = self._snapshot()

    @property
    def status(self) -> Status:
        return self._status

    def _snapshot(self, new_fall: bool = False) -> Status:
        return Status(self._state, self._since, self._burst_s, self._still_s, self.armed, new_fall)

    def _enter(self, state: State, t: float) -> None:
        self._state = state
        self._since = t
        if state in (State.MONITORING, State.MOVEMENT, State.NO_SIGNAL):
            self._burst_s = None
            self._still_s = None
        self._quiet_start = None

    def _start_burst(self, t: float) -> None:
        self._enter(State.BURST, t)
        self._burst_start = t
        self._last_motion = t
        self._burst_s = 0.0
        self._still_s = None

    def update(self, t: float, level: Level) -> Status:
        self._last_sample = t
        new_fall = False
        c = self.config
        if self._state is State.NO_SIGNAL:
            self._enter(State.MONITORING, t)

        if self._state is State.FALL:
            pass
        elif self._state is State.MONITORING:
            if level == "motion":
                self._start_burst(t)
        elif self._state is State.BURST:
            if level == "still":
                self._enter(State.STILLNESS, t)
                self._still_start = t
                self._still_s = 0.0
            else:
                self._last_motion = t
                self._burst_s = self._last_motion - self._burst_start
                if t - self._burst_start > c.burst_max_s:
                    self._enter(State.MOVEMENT, t)
        elif self._state is State.STILLNESS:
            elapsed = t - self._still_start
            if level == "motion" and elapsed <= c.burst_merge_s:
                self._state = State.BURST  # brief dip: same burst continues
                self._last_motion = t
                self._burst_s = self._last_motion - self._burst_start
                self._still_s = None
                if t - self._burst_start > c.burst_max_s:
                    self._enter(State.MOVEMENT, t)
            elif level != "still":
                self._enter(State.MOVEMENT, t)
            else:
                self._still_s = elapsed
                if elapsed >= c.still_s:
                    if self.armed:
                        self._enter(State.FALL, t)
                        self._still_s = elapsed
                        new_fall = True
                    else:
                        self._enter(State.MONITORING, t)
        elif self._state is State.MOVEMENT:
            if level == "still":
                if self._quiet_start is None:
                    self._quiet_start = t
                elif t - self._quiet_start >= c.quiet_reset_s:
                    self._enter(State.MONITORING, t)
            else:
                self._quiet_start = None

        self._status = self._snapshot(new_fall)
        return self._status

    def check_gap(self, t: float) -> Status:
        if (
            self._state not in (State.FALL, State.NO_SIGNAL)
            and self._last_sample is not None
            and t - self._last_sample > self.config.max_gap_s
        ):
            self._enter(State.NO_SIGNAL, t)
        self._status = self._snapshot()
        return self._status

    def acknowledge(self, t: float) -> Status:
        if self._state is State.FALL:
            self._enter(State.MONITORING, t)
        self._status = self._snapshot()
        return self._status

    def set_armed(self, armed: bool, t: float) -> Status:
        self.armed = armed
        self._status = self._snapshot()
        return self._status
