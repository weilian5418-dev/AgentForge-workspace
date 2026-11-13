"""Standard SSE byte framing for business and control frames (D12.5)."""

from __future__ import annotations

import json

from agentforge.contracts.event_stream import PublicEventEnvelope, SseControlFrame


def _sse_data_lines(body: str) -> str:
    """Split JSON into SSE data: lines; JSON already escaped newlines as \\n."""
    # json.dumps does not emit raw newlines inside strings; still guard against them.
    if "\r" in body:
        body = body.replace("\r", "\\r")
    lines = body.split("\n")
    return "".join(f"data: {line}\n" for line in lines)


def encode_business_event(envelope: PublicEventEnvelope) -> bytes:
    """Encode a public business event. Includes id (eventId); ends with blank line."""
    if "\n" in envelope.event_id or "\r" in envelope.event_id:
        raise ValueError("event_id must not contain CR/LF")
    if "\n" in envelope.type or "\r" in envelope.type:
        raise ValueError("event type must not contain CR/LF")
    body = json.dumps(
        envelope.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    frame = f"id: {envelope.event_id}\nevent: {envelope.type}\n{_sse_data_lines(body)}\n"
    return frame.encode("utf-8")


def encode_control_frame(frame: SseControlFrame) -> bytes:
    """Encode a connection control frame — no id, no forged business sequence."""
    if "\n" in frame.event or "\r" in frame.event:
        raise ValueError("control event name must not contain CR/LF")
    body = json.dumps(
        frame.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Intentionally omit `id:` so clients keep last business event id.
    text = f"event: {frame.event}\n{_sse_data_lines(body)}\n"
    return text.encode("utf-8")


def encode_heartbeat() -> bytes:
    """SSE comment heartbeat — no Event, no eventId, no sequence advance."""
    return b": heartbeat\n\n"
