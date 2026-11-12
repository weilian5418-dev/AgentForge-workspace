"""Project committed events to whitelisted public SSE envelopes (D12.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, get_args

from agentforge.contracts.event_stream import PublicEventEnvelope, StateInvalidatedPayload
from agentforge.contracts.events import EventType

# Types that must never expose raw payload on the public stream.
_INTERNAL_OR_SENSITIVE_TYPES = frozenset(
    {
        "native_event.ingested",
        "native_approval.requested",
        "native_approval.confirmed",
        "protocol.request_matched",
        "protocol.request_conflict",
        "message.delivered",
        "message.delivery_unknown",
    }
)

_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "secret",
        "token",
        "password",
        "bearer",
        "apikey",
        "api_key",
        "privatekey",
        "private_key",
        "authorization",
        "commandoutput",
        "command_output",
        "rawoutput",
        "raw_output",
        "absolutepath",
        "absolute_path",
        "credential",
        "cookie",
    }
)

_KNOWN_TYPES = frozenset(get_args(EventType))
_MAX_PUBLIC_PAYLOAD_BYTES = 64 * 1024


class ProjectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _key_sensitive(key: str) -> bool:
    lowered = key.replace("-", "").replace(".", "").lower()
    return any(frag in lowered for frag in _SENSITIVE_KEY_FRAGMENTS)


def _scrub(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str) or _key_sensitive(k):
                continue
            out[k] = _scrub(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_scrub(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, str) and len(value) > 4096:
        return value[:4096] + "…"
    return value


def _invalidate(
    *,
    event_id: str,
    run_id: str,
    sequence: int,
    timestamp: datetime,
    original_type: str,
    reason: str = "redacted",
) -> PublicEventEnvelope:
    payload = StateInvalidatedPayload.model_validate(
        {"originalType": original_type, "reason": reason}
    )
    return PublicEventEnvelope.model_validate(
        {
            "schemaVersion": "1",
            "eventId": event_id,
            "runId": run_id,
            "sequence": sequence,
            "type": "state.invalidated",
            "timestamp": timestamp,
            "payload": payload.model_dump(mode="json", by_alias=True),
        }
    )


def project_public_event(
    *,
    event_id: str,
    run_id: str,
    sequence: int,
    event_type: str,
    timestamp: datetime,
    payload: dict[str, Any] | None,
) -> PublicEventEnvelope:
    """Whitelist projection. Unknown types raise; internal types → state.invalidated."""
    if "\n" in event_id or "\r" in event_id:
        raise ProjectionError("cursor_invalid", "event_id must not contain CR/LF")
    if event_type not in _KNOWN_TYPES:
        raise ProjectionError("unknown_event_type", f"unknown event type {event_type}")
    if event_type == "resync_required":
        raise ProjectionError(
            "unknown_event_type",
            "resync_required is a control frame, not a business event projection",
        )

    if event_type in _INTERNAL_OR_SENSITIVE_TYPES or event_type == "state.invalidated":
        return _invalidate(
            event_id=event_id,
            run_id=run_id,
            sequence=sequence,
            timestamp=timestamp,
            original_type=event_type,
            reason="redacted" if event_type != "state.invalidated" else "redacted",
        )

    scrubbed = _scrub(dict(payload or {}))
    # Rough size guard — oversized → invalidated continuity frame
    import json

    encoded = json.dumps(scrubbed, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > _MAX_PUBLIC_PAYLOAD_BYTES:
        return _invalidate(
            event_id=event_id,
            run_id=run_id,
            sequence=sequence,
            timestamp=timestamp,
            original_type=event_type,
            reason="oversized",
        )

    return PublicEventEnvelope.model_validate(
        {
            "schemaVersion": "1",
            "eventId": event_id,
            "runId": run_id,
            "sequence": sequence,
            "type": event_type,
            "timestamp": timestamp,
            "payload": scrubbed,
        }
    )
