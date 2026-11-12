"""CLI-oriented SSE parser and cursor file helpers (D12.6)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedSseEvent:
    event: str | None
    data: str
    id: str | None
    comment: bool = False


@dataclass
class ClientRunProjection:
    run_id: str
    applied_sequence: int = 0
    last_applied_event_id: str | None = None
    schema_version: str = "1"


def parse_sse_stream(chunks: Iterator[bytes] | list[bytes]) -> Iterator[ParsedSseEvent]:
    """Parse SSE across chunk boundaries — do not treat one TCP packet as one event."""
    buffer = ""
    for chunk in chunks:
        if isinstance(chunk, bytes):
            buffer += chunk.decode("utf-8", errors="replace")
        else:
            buffer += str(chunk)
        while "\n\n" in buffer or "\r\n\r\n" in buffer:
            if "\r\n\r\n" in buffer and (
                "\n\n" not in buffer or buffer.index("\r\n\r\n") < buffer.index("\n\n")
            ):
                block, buffer = buffer.split("\r\n\r\n", 1)
                sep = "\r\n"
            else:
                block, buffer = buffer.split("\n\n", 1)
                sep = "\n"
            event = _parse_block(block, line_sep=sep)
            if event is not None:
                yield event


def _parse_block(block: str, *, line_sep: str) -> ParsedSseEvent | None:
    if not block.strip():
        return None
    event_name: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []
    only_comment = True
    for raw_line in block.split(line_sep):
        line = raw_line.rstrip("\r")
        if not line:
            continue
        if line.startswith(":"):
            continue
        only_comment = False
        if line.startswith("event:"):
            event_name = line[6:].lstrip()
        elif line.startswith("id:"):
            event_id = line[3:].lstrip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip() if line.startswith("data: ") else line[5:])
        elif line.startswith("retry:"):
            continue
    if only_comment:
        return ParsedSseEvent(event=None, data="", id=None, comment=True)
    return ParsedSseEvent(
        event=event_name,
        data="\n".join(data_lines),
        id=event_id,
        comment=False,
    )


def apply_public_event(projection: ClientRunProjection, payload: dict[str, Any]) -> bool:
    """Apply one public envelope; ignore duplicates; reject gaps. Returns True if applied."""
    if payload.get("runId") != projection.run_id:
        return False
    sequence = int(payload["sequence"])
    event_id = str(payload["eventId"])
    if sequence <= projection.applied_sequence:
        return False
    if sequence != projection.applied_sequence + 1:
        raise ValueError("event_gap_detected")
    projection.applied_sequence = sequence
    projection.last_applied_event_id = event_id
    return True


def reconnect_from_cursor(
    projection: ClientRunProjection,
) -> dict[str, str | int | None]:
    """Headers/query for reconnect — never includes bearer."""
    return {
        "runId": projection.run_id,
        "lastEventId": projection.last_applied_event_id,
        "afterSequence": projection.applied_sequence,
        "schemaVersion": projection.schema_version,
    }


def load_cursor_file(path: Path, *, run_id: str) -> ClientRunProjection:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("runId") != run_id:
        raise ValueError("cursor file runId mismatch")
    if "bearer" in raw or "token" in raw or "authorization" in raw:
        raise ValueError("cursor file must not contain secrets")
    return ClientRunProjection(
        run_id=run_id,
        applied_sequence=int(raw.get("lastAppliedSequence") or 0),
        last_applied_event_id=raw.get("lastAppliedEventId"),
        schema_version=str(raw.get("schemaVersion") or "1"),
    )


def save_cursor_file(path: Path, projection: ClientRunProjection) -> None:
    """Atomic write of cursor — no bearer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "runId": projection.run_id,
        "schemaVersion": projection.schema_version,
        "lastAppliedSequence": projection.applied_sequence,
        "lastAppliedEventId": projection.last_applied_event_id,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
