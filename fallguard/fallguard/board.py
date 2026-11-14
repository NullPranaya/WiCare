"""ESPectre board client: Server-Sent Events motion stream and health polling."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass

import httpx

ORIGIN = "https://test.espectre.dev"
# The board returns its field catalog unless a JSON array of field names is passed.
DIAGNOSTIC_FIELDS = '["csi_admitted_pps","csi_occupancy"]'


@dataclass(frozen=True)
class MotionSample:
    board: str
    t: float  # host monotonic seconds
    score: float
    device_ms: int


@dataclass(frozen=True)
class BoardStatus:
    board: str
    connected: bool
    detail: str


@dataclass(frozen=True)
class BoardHealth:
    board: str
    pps: float | None
    occupancy: float | None
    ready: bool | None


class _SseParser:
    def __init__(self) -> None:
        self._event = "message"
        self._data: list[str] = []

    def feed(self, line: str) -> tuple[str, dict] | None:
        line = line.rstrip("\r\n")
        if not line:
            event, data = self._event, self._data
            self._event, self._data = "message", []
            if not data:
                return None
            try:
                payload = json.loads("\n".join(data))
            except json.JSONDecodeError:
                return None
            return (event, payload) if isinstance(payload, dict) else None
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        return None


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    parser = _SseParser()
    for line in lines:
        event = parser.feed(line)
        if event is not None:
            yield event


class BoardClient:
    def __init__(
        self,
        board_id: str,
        endpoint: str,
        *,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
        backoff_s: tuple[float, ...] = (1, 2, 5),
    ) -> None:
        self.board_id = board_id
        self.endpoint = endpoint.rstrip("/")
        self.http = http
        self.clock = clock
        self.backoff_s = backoff_s or (1,)

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        return {"Origin": ORIGIN, "Accept": accept, "Cache-Control": "no-store"}

    async def stream(self) -> AsyncIterator[MotionSample | BoardStatus]:
        failures = 0
        while True:
            detail = "stream closed"
            try:
                async with self.http.stream(
                    "GET",
                    self.endpoint + "/events",
                    headers=self._headers("text/event-stream"),
                    timeout=httpx.Timeout(5.0, read=15.0),
                ) as response:
                    if response.status_code != 200:
                        detail = f"HTTP {response.status_code}"
                    else:
                        failures = 0
                        yield BoardStatus(self.board_id, True, "connected")
                        parser = _SseParser()
                        async for line in response.aiter_lines():
                            event = parser.feed(line)
                            if event is None or event[0] != "motion":
                                continue
                            data = event[1]
                            try:
                                score = float(data["score"])
                                device_ms = int(data.get("timestamp_ms", 0))
                            except (KeyError, TypeError, ValueError):
                                continue
                            yield MotionSample(self.board_id, self.clock(), score, device_ms)
            except httpx.HTTPError as exc:
                detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            yield BoardStatus(self.board_id, False, detail)
            delay = self.backoff_s[min(failures, len(self.backoff_s) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def _get(self, resource: str, params: dict[str, str] | None = None) -> dict | None:
        try:
            response = await self.http.get(
                f"{self.endpoint}/{resource}", params=params, headers=self._headers(), timeout=4.0
            )
            if response.status_code != 200:
                return None
            body = response.json()
            return body if isinstance(body, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    async def health(self) -> BoardHealth:
        diagnostics, sensing = await asyncio.gather(self._get("diagnostics", {"fields": DIAGNOSTIC_FIELDS}), self._get("sensing"))
        diagnostics = diagnostics or {}
        sensing = sensing or {}

        def num(key: str) -> float | None:
            value = diagnostics.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        ready = sensing.get("ready")
        return BoardHealth(
            self.board_id,
            num("csi_admitted_pps"),
            num("csi_occupancy"),
            ready if isinstance(ready, bool) else None,
        )
