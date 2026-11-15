import asyncio
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from fallguard.alerts import AlertManager
from fallguard.board import BoardStatus, MotionSample
from fallguard.config import AppConfig
from fallguard.server import create_app

WALL = datetime(2026, 11, 14, 14, 32, 5)


def make_config(tmp_path, boards=(("b1", "http://x/espectre/v1"),)):
    return AppConfig(boards=boards, data_dir=tmp_path)


def scripted(*segments, board="b1", start_delay=0.3, connect=True):
    """segments: (seconds, score) at 4 Hz on a fake timeline starting at t=0."""

    def source():
        async def gen():
            await asyncio.sleep(start_delay)  # let the test's websocket connect first
            if connect:
                yield BoardStatus(board, True, "connected")
            t = 0.0
            for seconds, score in segments:
                for _ in range(round(seconds * 4)):
                    yield MotionSample(board, t, score, int(t * 1000))
                    t = round(t + 0.25, 6)
                    await asyncio.sleep(0)
            await asyncio.Event().wait()  # stay connected

        return gen()

    return source


FALL_SCRIPT = ((1.0, 0.99), (9.0, 0.01))


def recorder():
    sent = []

    async def sender(text):
        sent.append(text)

    return sent, sender


def receive_until(ws, predicate, limit=500):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if predicate(msg):
            return msg, seen
    raise AssertionError(f"condition not met; last messages: {seen[-5:]}")


def build(tmp_path, sources, sender=None, **kw):
    alerts = AlertManager(sender, cooldown_s=60)
    return create_app(make_config(tmp_path), sources, None, alerts, clock=lambda: 0.0, wall=lambda: WALL, **kw)


def test_fall_flow_broadcasts_state_and_alert_once(tmp_path):
    sent, sender = recorder()
    with TestClient(build(tmp_path, [scripted(*FALL_SCRIPT)], sender)) as client:
        with client.websocket_connect("/ws") as ws:
            _, seen = receive_until(ws, lambda m: m["type"] == "alert" and m["imessage"] == "sent")
    falls = [m for m in seen if m["type"] == "state" and m["state"] == "FALL"]
    alerts = [m["imessage"] for m in seen if m["type"] == "alert"]
    assert len(falls) == 1 and alerts == ["sending", "sent"]
    assert sent == ["WiCare: possible fall detected at 14:32:05. Patient has been still for 8 s after a sudden movement.\n"
                    "Reply 1 to acknowledge, 2 to ignore."]


def test_board_state_label_is_ignored(tmp_path):
    # 0.80 is "motion" by the drifted board threshold (0.773) but below the app's 0.962.
    with TestClient(build(tmp_path, [scripted((3.0, 0.80), (1.0, 0.01))])) as client:
        with client.websocket_connect("/ws") as ws:
            _, seen = receive_until(ws, lambda m: m["type"] == "sample" and m["t"] == 3.75)
    assert not any(m["type"] == "state" and m["state"] == "BURST" for m in seen)


def test_new_socket_gets_current_status(tmp_path):
    with TestClient(build(tmp_path, [scripted(*FALL_SCRIPT)])) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "FALL")
        with client.websocket_connect("/ws") as ws2:
            first, second = ws2.receive_json(), ws2.receive_json()
    assert first["type"] == "config" and first["threshold"] == 0.962 and first["mode"] == "live"
    assert second["type"] == "state" and second["state"] == "FALL"


def test_alarm_broadcast_before_send_finishes(tmp_path):
    async def stuck_sender(text):
        await asyncio.Event().wait()

    with TestClient(build(tmp_path, [scripted(*FALL_SCRIPT)], stuck_sender)) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "FALL")
            msg, _ = receive_until(ws, lambda m: m["type"] == "alert")
    assert msg["imessage"] == "sending"


def test_ack_arm_and_mark(tmp_path):
    with TestClient(build(tmp_path, [scripted(*FALL_SCRIPT)])) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "FALL")
            assert client.post("/api/ack").json()["state"] == "MONITORING"
            assert client.post("/api/arm", json={"armed": False}).json()["armed"] is False
            assert client.post("/api/mark", json={"label": "fall"}).status_code == 200
            assert client.post("/api/mark", json={"label": "x"}).status_code == 422
    marks = [json.loads(line) for line in (tmp_path / "marks.jsonl").read_text().splitlines()]
    assert [m["label"] for m in marks] == ["fall"]


def test_session_log_written(tmp_path):
    with TestClient(build(tmp_path, [scripted((2.0, 0.01))])) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "sample" and m["t"] == 1.75)
    files = list((tmp_path / "sessions").glob("*.jsonl"))
    assert len(files) == 1
    lines = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert len(lines) == 8 and set(lines[0]) == {"t", "board", "score", "device_ms"}


def test_replay_mode_does_not_log_and_reports_mode(tmp_path):
    with TestClient(build(tmp_path, [scripted((1.0, 0.01))], mode="replay")) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["mode"] == "replay"
            receive_until(ws, lambda m: m["type"] == "sample" and m["t"] == 0.75)
    assert not (tmp_path / "sessions").exists()


def test_disconnect_status_forwarded(tmp_path):
    def source():
        async def gen():
            await asyncio.sleep(0.3)
            yield BoardStatus("b1", False, "HTTP 503")
            await asyncio.Event().wait()

        return gen()

    with TestClient(build(tmp_path, [source])) as client:
        with client.websocket_connect("/ws") as ws:
            msg, _ = receive_until(ws, lambda m: m["type"] == "board" and m["detail"] != "starting")
    assert msg["board"] == "b1" and msg["connected"] is False and msg["detail"] == "HTTP 503"


def test_new_socket_gets_board_snapshot(tmp_path):
    with TestClient(build(tmp_path, [])) as client:
        with client.websocket_connect("/ws") as ws:
            messages = [ws.receive_json() for _ in range(3)]
    assert messages[2] == {"type": "board", "board": "b1", "connected": False, "detail": "starting",
                           "pps": None, "occupancy": None, "ready": None}


def test_index_served(tmp_path):
    with TestClient(build(tmp_path, [])) as client:
        response = client.get("/")
    assert response.status_code == 200 and "WiCare" in response.text


def test_websocket_library_installed_for_uvicorn():
    # uvicorn rejects WebSocket upgrades ("Unsupported upgrade request") without one of these.
    import importlib.util

    assert importlib.util.find_spec("websockets") or importlib.util.find_spec("wsproto")


def test_two_boards_one_noisy_no_fall(tmp_path):
    config = make_config(tmp_path, boards=(("a", "http://x/espectre/v1"), ("b", "http://y/espectre/v1")))
    sent, sender = recorder()
    app = create_app(
        config,
        [scripted(*FALL_SCRIPT, board="a"), scripted((10.0, 0.01), board="b")],
        None,
        AlertManager(sender, cooldown_s=60),
        clock=lambda: 0.0,
        wall=lambda: WALL,
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _, seen = receive_until(ws, lambda m: m["type"] == "sample" and m["board"] == "a" and m["t"] == 9.75)
    assert not any(m["type"] == "state" and m["state"] == "FALL" for m in seen)
    assert sent == []


def test_two_boards_agreeing_fall(tmp_path):
    config = make_config(tmp_path, boards=(("a", "http://x/espectre/v1"), ("b", "http://y/espectre/v1")))
    app = create_app(
        config,
        [scripted(*FALL_SCRIPT, board="a"), scripted(*FALL_SCRIPT, board="b")],
        None,
        AlertManager(None, cooldown_s=60),
        clock=lambda: 0.0,
        wall=lambda: WALL,
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "FALL")


def test_no_data_shows_no_signal(tmp_path):
    with TestClient(build(tmp_path, [])) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert ws.receive_json()["state"] == "NO_SIGNAL"


def test_source_crash_reports_disconnect_and_restarts(tmp_path):
    # review finding: an unexpected error must not end ingestion for good
    runs = {"n": 0}

    def source():
        async def gen():
            runs["n"] += 1
            await asyncio.sleep(0.3)
            yield BoardStatus("b1", True, "connected")
            if runs["n"] == 1:
                yield MotionSample("b1", 1.0, 0.01, 1000)
                raise RuntimeError("stream broke")
            yield MotionSample("b1", 99.0, 0.01, 99000)
            await asyncio.Event().wait()

        return gen()

    app = create_app(make_config(tmp_path), [source], None, AlertManager(None, 60),
                     clock=lambda: 0.0, wall=lambda: WALL, restart_delay_s=0.0)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _, seen = receive_until(ws, lambda m: m["type"] == "sample" and m["t"] == 99.0)
    crashed = [m for m in seen if m["type"] == "board" and not m["connected"] and "stream broke" in m["detail"]]
    assert crashed and runs["n"] == 2


# --- nurse replies and the room view --------------------------------------
class FakeReplies:
    def __init__(self) -> None:
        self.armed = 0
        self.queue: list[str] = []

    def arm(self) -> None:
        self.armed += 1

    async def poll(self) -> list[str]:
        out, self.queue = self.queue, []
        return out


def build_with_replies(tmp_path, replies, sender=None):
    if sender is None:
        _, sender = recorder()
    alerts = AlertManager(sender, cooldown_s=60)
    return create_app(make_config(tmp_path), [scripted(*FALL_SCRIPT)], None, alerts, clock=lambda: 0.0,
                      wall=lambda: WALL, replies=replies)


def test_nurse_reply_1_acknowledges_and_routes_to_room(tmp_path, monkeypatch):
    monkeypatch.setattr("fallguard.server.REPLY_POLL_S", 0.01)
    replies = FakeReplies()
    with TestClient(build_with_replies(tmp_path, replies)) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "alert" and m["imessage"] == "sent")
            assert replies.armed == 1
            replies.queue.append("acknowledge")
            _, seen = receive_until(ws, lambda m: m["type"] == "route")
    states = [m["state"] for m in seen if m["type"] == "state"]
    alerts = [m["imessage"] for m in seen if m["type"] == "alert"]
    assert states[-1] == "MONITORING" and alerts == ["acknowledged"]
    assert seen[-1] == {"type": "route", "to": "/room"}
    assert not (tmp_path / "marks.jsonl").exists()


def test_nurse_reply_2_ignores_and_saves_false_alarm(tmp_path, monkeypatch):
    monkeypatch.setattr("fallguard.server.REPLY_POLL_S", 0.01)
    replies = FakeReplies()
    with TestClient(build_with_replies(tmp_path, replies)) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "alert" and m["imessage"] == "sent")
            replies.queue.append("ignore")
            _, seen = receive_until(ws, lambda m: m["type"] == "alert" and m["imessage"] == "ignored")
    assert [m["state"] for m in seen if m["type"] == "state"] == ["MONITORING"]
    assert not any(m["type"] == "route" for m in seen)
    marks = [json.loads(line) for line in (tmp_path / "marks.jsonl").read_text().splitlines()]
    assert [(m["label"], m["source"]) for m in marks] == [("false_alarm", "nurse_reply")]


def test_reply_after_dashboard_ack_changes_nothing(tmp_path):
    replies = FakeReplies()
    app = build_with_replies(tmp_path, replies)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "alert" and m["imessage"] == "sent")
            client.post("/api/ack")
            engine = app.state.engine
            engine.on_reply("acknowledge")
            assert engine.last_alert["imessage"] == "sent"


def test_failed_send_does_not_wait_for_reply(tmp_path):
    async def broken(text):
        raise RuntimeError("Photon down")

    replies = FakeReplies()
    app = build_with_replies(tmp_path, replies, sender=broken)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, lambda m: m["type"] == "alert" and m["imessage"] == "failed")
    assert replies.armed == 0 and app.state.engine.awaiting_reply is False


def test_room_page_served(tmp_path):
    with TestClient(build(tmp_path, [])) as client:
        response = client.get("/room")
    assert response.status_code == 200 and "Room view" in response.text


def test_ward_page_links_bed_one_to_dashboard(tmp_path):
    with TestClient(build(tmp_path, [])) as client:
        response = client.get("/ward")
    assert response.status_code == 200
    assert 'id="bed-live" href="/"' in response.text and "/static/ward.js" in response.text


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css", "/room", "/ward", "/static/ward.js"])
def test_pages_and_assets_are_revalidated(tmp_path, path):
    with TestClient(build(tmp_path, [])) as client:
        response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
