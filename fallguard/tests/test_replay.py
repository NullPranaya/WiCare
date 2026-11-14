import json

from fallguard.board import BoardStatus, MotionSample
from fallguard.replay import ReplaySource


def write_session(path, rows, bad_line=False):
    lines = [json.dumps({"t": t, "board": "b1", "score": s, "device_ms": int(t * 1000)}) for t, s in rows]
    if bad_line:
        lines.insert(1, "{not json")
    path.write_text("\n".join(lines) + "\n")


async def collect(source):
    return [item async for item in source()]


def recording_sleep(log):
    async def sleep(seconds):
        log.append(round(seconds, 6))

    return sleep


async def test_replay_preserves_order_and_gaps(tmp_path):
    path = tmp_path / "s.jsonl"
    write_session(path, [(10.0, 0.1), (10.25, 0.9), (11.25, 0.2)])
    for speed, expected in ((1.0, [0.25, 1.0]), (2.0, [0.125, 0.5])):
        sleeps: list[float] = []
        items = await collect(ReplaySource(path, speed=speed, sleep=recording_sleep(sleeps), clock=lambda: 5.0))
        assert sleeps == expected
        assert items[0] == BoardStatus("b1", True, "replay")
        assert [i.score for i in items[1:-1]] == [0.1, 0.9, 0.2]
        assert all(isinstance(i, MotionSample) and i.t == 5.0 for i in items[1:-1])


async def test_replay_skips_bad_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    write_session(path, [(0.0, 0.1), (0.25, 0.2)], bad_line=True)
    items = await collect(ReplaySource(path, sleep=recording_sleep([]), clock=lambda: 0.0))
    assert [i.score for i in items[1:-1]] == [0.1, 0.2]


async def test_replay_reports_finished_at_end(tmp_path):
    path = tmp_path / "s.jsonl"
    write_session(path, [(0.0, 0.1), (0.25, 0.2)])
    items = await collect(ReplaySource(path, sleep=recording_sleep([]), clock=lambda: 0.0))
    assert items[-1] == BoardStatus("b1", False, "replay finished")
