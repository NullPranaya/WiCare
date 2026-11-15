"""Room view routes. The dashboard is sent here when the nurse acknowledges a fall.

Placeholder for now: the 3D model of the room showing the patient's position goes here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

ROOM_PATH = "/room"
STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter()


@router.get(ROOM_PATH)
async def room() -> FileResponse:
    return FileResponse(STATIC_DIR / "room.html", headers={"Cache-Control": "no-cache"})
