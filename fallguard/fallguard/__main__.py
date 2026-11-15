"""Run FallGuard: python -m fallguard [--port N] [--replay PATH]."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv

from fallguard.alerts import AlertManager, NodeSender
from fallguard.board import BoardClient
from fallguard.config import load_config
from fallguard.server import create_app

PROJECT_DIR = Path(__file__).resolve().parent.parent


def build_app(replay: Path | None = None):
    load_dotenv(PROJECT_DIR / ".env")  # also exports SPECTRUM_* to the Node sender
    config = load_config(os.environ, PROJECT_DIR)
    sender = (
        NodeSender(PROJECT_DIR / "notify" / "send.mjs", config.nurse_phone) if config.nurse_phone else None
    )
    alerts = AlertManager(sender, config.cooldown_s)
    if replay is not None:
        from fallguard.replay import ReplaySource

        return config, create_app(config, [ReplaySource(replay)], None, alerts, mode="replay")

    http = httpx.AsyncClient()
    clients = [BoardClient(board_id, endpoint, http=http) for board_id, endpoint in config.boards]

    async def health():
        return list(await asyncio.gather(*(c.health() for c in clients)))

    return config, create_app(config, [c.stream for c in clients], health, alerts)


def lan_address() -> str:
    """This machine's address on the local network (a UDP connect sends no packets)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(prog="fallguard")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--replay", type=Path, default=None, help="replay a recorded session .jsonl")
    args = parser.parse_args()
    config, app = build_app(args.replay)
    port = args.port or config.port
    shown = lan_address() if config.host in ("0.0.0.0", "") else config.host
    print(f"FallGuard dashboard: http://{shown}:{port}/", flush=True)
    uvicorn.run(app, host=config.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
