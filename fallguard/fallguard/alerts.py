"""Fall alerts: iMessage via Photon (Node script) with a cooldown."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

AlertStatus = Literal["sent", "failed", "skipped_cooldown", "no_recipient"]


@dataclass(frozen=True)
class AlertResult:
    status: AlertStatus
    detail: str


def fall_message(at: datetime, still_s: float) -> str:
    return (
        f"WiCare: possible fall detected at {at:%H:%M:%S}. "
        f"Patient has been still for {still_s:.0f} s after a sudden movement.\n"
        "Reply 1 to acknowledge, 2 to ignore."
    )


class NodeSender:
    """Runs `node send.mjs <recipient> <text>`; credentials come from the environment."""

    def __init__(
        self,
        script: Path,
        recipient: str,
        *,
        command: Sequence[str] = ("node",),
        timeout_s: float = 20.0,
    ) -> None:
        self.script = script
        self.recipient = recipient
        self.command = tuple(command)
        self.timeout_s = timeout_s

    async def __call__(self, text: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *self.command,
            str(self.script),
            self.recipient,
            text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), self.timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"iMessage sender timed out after {self.timeout_s:.0f} s") from None
        if process.returncode != 0:
            tail = stderr.decode(errors="replace").strip()[-300:]
            raise RuntimeError(tail or f"iMessage sender exited with {process.returncode}")


class AlertManager:
    def __init__(
        self,
        sender: Callable[[str], Awaitable[None]] | None,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sender = sender
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._last_sent: float | None = None

    async def on_fall(self, at: datetime, still_s: float) -> AlertResult:
        if self.sender is None:
            return AlertResult("no_recipient", "NURSE_PHONE is not set")
        now = self.clock()
        if self._last_sent is not None and now - self._last_sent < self.cooldown_s:
            remaining = self.cooldown_s - (now - self._last_sent)
            return AlertResult("skipped_cooldown", f"cooldown, {remaining:.0f} s left")
        try:
            await self.sender(fall_message(at, still_s))
        except Exception as exc:  # any sender failure is reported, never raised
            return AlertResult("failed", str(exc) or type(exc).__name__)
        self._last_sent = self.clock()
        return AlertResult("sent", "delivered to Photon")
