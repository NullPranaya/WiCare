"""FastAPI app: board samples -> fall rule -> dashboard WebSocket and iMessage alerts."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fallguard.alerts import AlertManager
from fallguard.board import BoardHealth, BoardStatus, MotionSample
from fallguard.config import AppConfig
from fallguard.detector import FallDetector, Status, level_for
from fallguard.fusion import Fusion
from fallguard.replies import Reply
from fallguard.room import ROOM_PATH
from fallguard.room import router as room_router

SampleSource = Callable[[], AsyncIterator[MotionSample | BoardStatus]]
HealthSource = Callable[[], Awaitable[list[BoardHealth]]]
Mode = Literal["live", "replay"]


class ReplySource(Protocol):
    def arm(self) -> None: ...
    async def poll(self) -> list[Reply]: ...


STATIC_DIR = Path(__file__).parent / "static"
TICK_S = 0.25
HEALTH_S = 2.0
REPLY_POLL_S = 2.0


class ArmRequest(BaseModel):
    armed: bool


class MarkRequest(BaseModel):
    label: Literal["fall", "false_alarm"]


class Engine:
    def __init__(self, config, alerts, mode, clock, wall, replies: ReplySource | None = None) -> None:
        self.config = config
        self.alerts = alerts
        self.replies = replies
        self.awaiting_reply = False  # an alert went out and the nurse has not answered yet
        self.mode = mode
        self.clock = clock
        self.wall = wall
        self.detector = FallDetector(config.rule)
        board_ids = [b for b, _ in config.boards]
        self.fusion = (
            Fusion(board_ids, config.rule.threshold, max_gap_s=config.rule.max_gap_s)
            if len(board_ids) > 1
            else None
        )
        self.clients: set[asyncio.Queue] = set()
        self.boards: dict[str, dict] = {
            board_id: {"type": "board", "board": board_id, "connected": False, "detail": "starting",
                       "pps": None, "occupancy": None, "ready": None}
            for board_id, _ in config.boards
        }
        self.last_alert: dict | None = None
        self.tasks: set[asyncio.Task] = set()
        self._session = None

    # --- messages -------------------------------------------------------
    def config_message(self) -> dict:
        rule = self.config.rule
        return {"type": "config", "threshold": rule.threshold, "burst_max_s": rule.burst_max_s,
                "still_s": rule.still_s, "mode": self.mode, "boards": [b for b, _ in self.config.boards]}

    def state_message(self, status: Status | None = None) -> dict:
        s = status or self.detector.status
        return {"type": "state", "state": s.state.value, "since": s.since, "burst_s": s.burst_s,
                "still_s": s.still_s, "armed": s.armed, "now": self.clock()}

    def broadcast(self, message: dict) -> None:
        for queue in list(self.clients):
            queue.put_nowait(message)

    # --- inputs ---------------------------------------------------------
    def _log_sample(self, sample: MotionSample) -> None:
        if self.mode != "live":
            return
        if self._session is None:
            sessions = self.config.data_dir / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            self._session = open(sessions / f"{self.wall():%Y%m%d-%H%M%S}.jsonl", "a", buffering=1)
        self._session.write(json.dumps({"t": sample.t, "board": sample.board, "score": sample.score,
                                        "device_ms": sample.device_ms}) + "\n")

    def handle(self, item: MotionSample | BoardStatus) -> None:
        if isinstance(item, BoardStatus):
            board = self.boards.setdefault(item.board, {"type": "board", "board": item.board})
            board.update(connected=item.connected, detail=item.detail)
            if not item.connected and self.fusion is not None:
                self.fusion.disconnect(item.board)
            self.broadcast(dict(board))
            return
        self._log_sample(item)
        self.broadcast({"type": "sample", "board": item.board, "t": item.t, "score": item.score})
        if self.fusion is not None:
            level = self.fusion.update(item)
            if level is None:
                return
        else:
            level = level_for(item.score, self.config.rule.threshold)
        previous = self.detector.status
        self._publish(previous, self.detector.update(item.t, level))

    def _publish(self, previous: Status, status: Status) -> None:
        if status.state is not previous.state or status.new_fall or status.armed is not previous.armed:
            self.broadcast(self.state_message(status))
        if status.new_fall:
            self._alert_status("sending", "contacting Photon")
            task = asyncio.create_task(self._send_alert(status))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def _alert_status(self, imessage: str, detail: str) -> None:
        self.last_alert = {"type": "alert", "imessage": imessage, "detail": detail,
                           "at": f"{self.wall():%H:%M:%S}"}
        self.broadcast(self.last_alert)

    async def _send_alert(self, status: Status) -> None:
        result = await self.alerts.on_fall(self.wall(), status.still_s or self.config.rule.still_s)
        if result.status == "sent" and self.replies is not None:
            self.replies.arm()
            self.awaiting_reply = True
        self._alert_status(result.status, result.detail)

    def on_reply(self, reply: Reply) -> None:
        """The nurse answered by text: 1 acknowledges (open the room view), 2 ignores (false alarm)."""
        if not self.awaiting_reply:
            return
        self.awaiting_reply = False
        before = self.detector.status
        self._publish(before, self.detector.acknowledge(self.clock()))
        if reply == "acknowledge":
            self._alert_status("acknowledged", "nurse replied 1")
            self.broadcast({"type": "route", "to": ROOM_PATH})
        else:
            self._alert_status("ignored", "nurse replied 2")
            self.write_mark("false_alarm", "nurse_reply")

    def write_mark(self, label: str, source: str) -> dict:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        record = {"wall": self.wall().isoformat(timespec="seconds"), "t": self.clock(), "label": label,
                  "state": self.detector.status.state.value, "source": source}
        with open(self.config.data_dir / "marks.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        return record

    def tick(self) -> None:
        previous = self.detector.status
        self._publish(previous, self.detector.check_gap(self.clock()))

    def apply_health(self, health: list[BoardHealth]) -> None:
        for h in health:
            board = self.boards.setdefault(h.board, {"type": "board", "board": h.board, "connected": False})
            board.update(pps=h.pps, occupancy=h.occupancy, ready=h.ready)
            self.broadcast(dict(board))

    def command(self, status_before: Status, status_after: Status) -> dict:
        self._publish(status_before, status_after)
        return self.state_message(status_after)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()


def create_app(
    config: AppConfig,
    sources: list[SampleSource],
    health: HealthSource | None,
    alerts: AlertManager,
    *,
    mode: Mode = "live",
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], datetime] = datetime.now,
    restart_delay_s: float = 1.0,
    replies: ReplySource | None = None,
) -> FastAPI:
    engine = Engine(config, alerts, mode, clock, wall, replies)

    async def consume(source: SampleSource) -> None:
        """Run a source; on an unexpected error mark its board offline and restart it."""
        board_id: str | None = None
        while True:
            try:
                async for item in source():
                    board_id = item.board
                    engine.handle(item)
                return  # a finite source (replay) ended normally
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep ingesting no matter what broke
                if board_id is not None:
                    engine.handle(BoardStatus(board_id, False, f"{type(exc).__name__}: {exc}"))
                await asyncio.sleep(restart_delay_s)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(TICK_S)
            engine.tick()

    async def poll_health() -> None:
        while True:
            with contextlib.suppress(Exception):
                engine.apply_health(await health())
            await asyncio.sleep(HEALTH_S)

    async def poll_replies() -> None:
        while True:
            await asyncio.sleep(REPLY_POLL_S)
            if not engine.awaiting_reply:
                continue
            with contextlib.suppress(Exception):  # a failed poll is retried on the next tick
                for reply in await replies.poll():
                    engine.on_reply(reply)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loops = [consume(s) for s in sources] + [ticker()]
        if health is not None:
            loops.append(poll_health())
        if replies is not None:
            loops.append(poll_replies())
        background = [asyncio.create_task(coro) for coro in loops]
        try:
            yield
        finally:
            for task in background + list(engine.tasks):
                task.cancel()
            await asyncio.gather(*background, *engine.tasks, return_exceptions=True)
            engine.close()

    app = FastAPI(lifespan=lifespan)
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(room_router)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        engine.clients.add(queue)
        try:
            await socket.send_json(engine.config_message())
            await socket.send_json(engine.state_message())
            for board in engine.boards.values():
                await socket.send_json(dict(board))
            if engine.last_alert is not None:
                await socket.send_json(engine.last_alert)
            while True:
                await socket.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            engine.clients.discard(queue)

    @app.post("/api/ack")
    async def ack() -> dict:
        engine.awaiting_reply = False  # answered on the dashboard; a later text reply changes nothing
        before = engine.detector.status
        return engine.command(before, engine.detector.acknowledge(clock()))

    @app.post("/api/arm")
    async def arm(body: ArmRequest) -> dict:
        before = engine.detector.status
        return engine.command(before, engine.detector.set_armed(body.armed, clock()))

    @app.post("/api/mark")
    async def mark(body: MarkRequest) -> dict:
        return engine.write_mark(body.label, "dashboard")

    return app
