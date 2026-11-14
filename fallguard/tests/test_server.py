import asyncio
import json
from datetime import datetime

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
    assert sent == ["FallGuard: possible fall detected at 14:32:05. Patient has been still for 8 s after a sudden movement."]


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
    assert response.status_code == 200 and "FallGuard" in response.text
