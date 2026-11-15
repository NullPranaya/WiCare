from fallguard.board import MotionSample
from fallguard.fusion import Fusion

THR = 0.962


def s(board, t, score):
    return MotionSample(board, t, score, 0)


def test_none_until_all_reported():
    f = Fusion(["a", "b"], THR)
    assert f.update(s("a", 0.0, 0.99)) is None
    assert f.update(s("b", 0.1, 0.01)) is not None


def test_motion_needs_all_boards():
    f = Fusion(["a", "b"], THR)
    f.update(s("b", 0.0, 0.01))
    assert f.update(s("a", 0.1, 0.99)) == "mixed"
    assert f.update(s("b", 0.6, 0.99)) == "motion"


def test_still_needs_all_boards():
    f = Fusion(["a", "b"], THR)
    f.update(s("a", 0.0, 0.01))
    assert f.update(s("b", 0.1, 0.01)) == "still"
    assert f.update(s("a", 0.2, 0.99)) == "mixed"


def test_window_expires():
    f = Fusion(["a", "b"], THR)
    f.update(s("a", 0.0, 0.99))
    f.update(s("a", 0.25, 0.01))
    assert f.update(s("b", 1.2, 0.99)) == "mixed"


def test_stale_board_gives_none():
    # a silent board must not count as still (review finding: false FALL on one board's data)
    f = Fusion(["a", "b"], THR, max_gap_s=1.5)
    f.update(s("a", 0.0, 0.01))
    f.update(s("b", 0.0, 0.01))
    assert f.update(s("a", 1.0, 0.01)) == "still"
    assert f.update(s("a", 2.0, 0.01)) is None


def test_disconnected_board_gives_none_until_it_reports():
    f = Fusion(["a", "b"], THR)
    f.update(s("a", 0.0, 0.01))
    f.update(s("b", 0.0, 0.01))
    f.disconnect("b")
    assert f.update(s("a", 0.25, 0.01)) is None
    assert f.update(s("b", 0.5, 0.01)) == "still"
