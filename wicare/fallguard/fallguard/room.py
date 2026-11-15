"""Room view routes. The dashboard is sent here when the nurse acknowledges a fall.

In the AuraCare repo the room view is AuraCare's 3D room view (auracare/frontend, ?room=1): a hospital room
where the patient's movement is shown as a figure. It is served from this app at /room3d/, so one server runs
everything. Without that folder (WiCare on its own) the placeholder page is used.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOM_PATH = "/room"
STATIC_DIR = Path(__file__).parent / "static"
ROOM_3D_PATH = "/room3d"
# auracare/wicare/fallguard/fallguard/room.py -> auracare/frontend
ROOM_3D_DIR = Path(__file__).resolve().parents[3] / "frontend"

router = APIRouter()


def has_room_3d() -> bool:
    return (ROOM_3D_DIR / "index.html").is_file()


def mount_room_3d(app: FastAPI) -> None:
    if has_room_3d():
        app.mount(ROOM_3D_PATH, StaticFiles(directory=ROOM_3D_DIR, html=True), name="room3d")


@router.get(ROOM_PATH)
async def room():
    if has_room_3d():
        return RedirectResponse(f"{ROOM_3D_PATH}/?room=1")
    return FileResponse(STATIC_DIR / "room.html", headers={"Cache-Control": "no-cache"})
