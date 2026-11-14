from dataclasses import dataclass, field

from fallguard.config import RuleConfig
from fallguard.detector import FallDetector, State, Status, level_for

STEP = 0.25


@dataclass
class Fed:
    last: Status | None = None
    falls: list[Status] = field(default_factory=list)


def feed(det: FallDetector, start: float, end: float, level: str) -> Fed:
    """Feed samples at 4 Hz on [start, end)."""
    out = Fed()
    n = round((end - start) / STEP)
    for i in range(n):
        s = det.update(round(start + i * STEP, 6), level)
        out.last = s
        if s.new_fall:
            out.falls.append(s)
    return out


def test_level_for_threshold_is_inclusive():
    assert level_for(0.962, 0.962) == "motion"
    assert level_for(0.961, 0.962) == "still"


def test_fall_fires_after_short_burst_then_stillness():
    d = FallDetector(RuleConfig())
    feed(d, 0.0, 2.0, "still")
    feed(d, 2.0, 3.0, "motion")  # motion samples 2.0..2.75
    falls = feed(d, 3.0, 12.0, "still").falls
    assert [round(s.since, 2) for s in falls] == [11.0]  # 3.0 + 8.0
    assert falls[0].state is State.FALL and abs(falls[0].burst_s - 0.75) < 1e-9


def test_long_burst_is_movement_not_fall():
    d = FallDetector(RuleConfig())
    feed(d, 0.0, 4.0, "motion")
    assert d.status.state is State.MOVEMENT
    assert feed(d, 4.0, 14.0, "still").falls == []


def test_motion_during_stillness_is_movement():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    feed(d, 1, 4, "still")
    feed(d, 4, 4.5, "motion")
    assert d.status.state is State.MOVEMENT
    assert feed(d, 4.5, 14, "still").falls == []


def test_brief_dip_merges_into_burst():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    feed(d, 1, 1.5, "still")
    feed(d, 1.5, 2, "motion")
    assert d.status.state is State.BURST


def test_disarmed_never_falls():
    d = FallDetector(RuleConfig(), armed=False)
    feed(d, 0, 1, "motion")
    assert feed(d, 1, 12, "still").falls == [] and d.status.state is State.MONITORING


def test_gap_cancels_stillness():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    feed(d, 1, 4, "still")
    assert d.check_gap(5.6).state is State.NO_SIGNAL
    assert feed(d, 5.75, 15, "still").falls == []


def test_fall_latches_until_acknowledge():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    feed(d, 1, 10, "still")
    feed(d, 10, 12, "motion")
    assert d.check_gap(30).state is State.FALL
    assert d.acknowledge(31).state is State.MONITORING


def test_new_fall_only_once():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    assert len(feed(d, 1, 20, "still").falls) == 1


def test_mixed_during_stillness_is_movement():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    feed(d, 1, 3, "still")
    d.update(3.0, "mixed")
    assert d.status.state is State.MOVEMENT


def test_disarm_mid_stillness_suppresses_fall():
    d = FallDetector(RuleConfig())
    feed(d, 0, 1, "motion")
    feed(d, 1, 3, "still")
    d.set_armed(False, 3.0)
    assert feed(d, 3, 12, "still").falls == [] and d.status.state is State.MONITORING


def test_starts_in_no_signal_until_first_sample():
    # review finding: with no data the dashboard must not claim "Monitoring"
    d = FallDetector(RuleConfig())
    assert d.status.state is State.NO_SIGNAL
    assert d.update(0.0, "still").state is State.MONITORING
