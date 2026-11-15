"""Nurse replies to a fall alert, read from Photon: "1" acknowledges, "2" ignores."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx

Reply = Literal["acknowledge", "ignore"]

API = "https://api.photon.codes"
SKEW = timedelta(seconds=10)  # tolerate a little clock difference between the laptop and Photon
MAX_PAGES = 5

_ACKNOWLEDGE = {"1", "1️⃣", "ack", "acknowledge", "acknowledged"}
_IGNORE = {"2", "2️⃣", "ignore", "ignored"}


def parse_reply(text: str) -> Reply | None:
    word = text.strip().lower().rstrip(".!")
    if word in _ACKNOWLEDGE:
        return "acknowledge"
    if word in _IGNORE:
        return "ignore"
    return None


def _digits(handle: str) -> str:
    return re.sub(r"\D", "", handle or "")


class PhotonReplies:
    """Polls the project's received messages for the nurse's answer to the latest alert."""

    def __init__(
        self,
        project_id: str,
        api_key: str,
        nurse_handle: str,
        *,
        http: httpx.AsyncClient,
        api: str = API,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.url = f"{api.rstrip('/')}/v1/projects/{project_id}/messages"
        self.headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self.nurse = _digits(nurse_handle)
        self.http = http
        self.now = now
        self._since: datetime | None = None
        self._seen: set[str] = set()

    def arm(self) -> None:
        """Only replies sent from now on count as answers to the alert just sent."""
        self._since = self.now() - SKEW
        self._seen.clear()

    async def poll(self) -> list[Reply]:
        if self._since is None:
            return []
        params = {
            "direction": "received",
            "platform": "imessage",
            "order": "asc",
            "pageSize": "100",
            "createdSince": self._since.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        replies: list[Reply] = []
        for _ in range(MAX_PAGES):
            response = await self.http.get(self.url, params=params, headers=self.headers, timeout=8.0)
            response.raise_for_status()
            body = response.json()
            for item in body.get("items") or []:
                message_id = item.get("id")
                if not message_id or message_id in self._seen:
                    continue
                self._seen.add(message_id)
                if _digits((item.get("sentBy") or {}).get("handle", "")) != self.nurse:
                    continue
                content = item.get("content") or {}
                reply = parse_reply(content.get("text") or "") if content.get("type") == "text" else None
                if reply is not None:
                    replies.append(reply)
            token = body.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return replies
