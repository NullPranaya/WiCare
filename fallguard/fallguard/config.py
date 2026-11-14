"""Configuration loaded from environment variables (normally from fallguard/.env)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BOARDS = "b1=http://172.20.10.3:62587/espectre/v1"


@dataclass(frozen=True)
class RuleConfig:
    threshold: float = 0.962
    burst_max_s: float = 3.0
    still_s: float = 8.0
    burst_merge_s: float = 0.75
    quiet_reset_s: float = 1.0
    max_gap_s: float = 1.5


@dataclass(frozen=True)
class AppConfig:
    boards: tuple[tuple[str, str], ...]
    data_dir: Path
    rule: RuleConfig = field(default_factory=RuleConfig)
    cooldown_s: float = 60.0
    nurse_phone: str | None = None
    port: int = 8000


def _parse_boards(raw: str) -> tuple[tuple[str, str], ...]:
    boards: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        board_id, sep, endpoint = entry.partition("=")
        board_id, endpoint = board_id.strip(), endpoint.strip()
        if not sep or not board_id or not endpoint.startswith("http"):
            raise ValueError(f"invalid FALLGUARD_BOARDS entry: {entry!r} (expected id=http://...)")
        boards.append((board_id, endpoint.rstrip("/")))
    if not boards:
        raise ValueError("FALLGUARD_BOARDS lists no boards")
    return tuple(boards)


def load_config(env: Mapping[str, str], base_dir: Path) -> AppConfig:
    defaults = RuleConfig()
    rule = RuleConfig(
        threshold=float(env.get("FALLGUARD_THRESHOLD", defaults.threshold)),
        burst_max_s=float(env.get("FALLGUARD_BURST_MAX_S", defaults.burst_max_s)),
        still_s=float(env.get("FALLGUARD_STILL_S", defaults.still_s)),
    )
    return AppConfig(
        boards=_parse_boards(env.get("FALLGUARD_BOARDS", DEFAULT_BOARDS)),
        data_dir=Path(base_dir) / "data",
        rule=rule,
        cooldown_s=float(env.get("FALLGUARD_COOLDOWN_S", 60.0)),
        nurse_phone=(env.get("NURSE_PHONE") or "").strip() or None,
        port=int(env.get("FALLGUARD_PORT", 8000)),
    )
