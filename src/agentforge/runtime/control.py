"""Admission control file shared by CLI and API (drain / open / blocked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from agentforge.core.config import Settings

Admission = Literal["open", "drain", "blocked"]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read_admission(settings: Settings) -> Admission:
    """Read platform admission.

    - Missing control file → ``open`` (never created).
    - Corrupt / unreadable / unknown content → ``blocked`` (fail-closed; no new work).
    """
    path = settings.control_path
    if not path.exists():
        return "open"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return "blocked"
    if not isinstance(data, dict):
        return "blocked"
    value = data.get("admission")
    if value == "drain":
        return "drain"
    if value == "blocked":
        return "blocked"
    if value == "open":
        return "open"
    # Unknown or missing admission field while file exists → do not admit work.
    return "blocked"


def write_admission(settings: Settings, admission: Admission) -> dict[str, Any]:
    if admission not in {"open", "drain", "blocked"}:
        raise ValueError(f"Unsupported admission value: {admission}")
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {"admission": admission, "updatedAt": _iso_now()}
    path = settings.control_path
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def admits_new_work(admission: Admission) -> bool:
    return admission == "open"
