"""SSE cursor resolution, public projection, framing, and stream service (D12)."""

from agentforge.streaming.client import (
    ClientRunProjection,
    parse_sse_stream,
    reconnect_from_cursor,
)
from agentforge.streaming.cursor import CursorError, resolve_event_cursor
from agentforge.streaming.framing import (
    encode_business_event,
    encode_control_frame,
    encode_heartbeat,
)
from agentforge.streaming.projector import ProjectionError, project_public_event
from agentforge.streaming.service import EventStreamService, read_bounded_batch

__all__ = [
    "ClientRunProjection",
    "CursorError",
    "EventStreamService",
    "ProjectionError",
    "encode_business_event",
    "encode_control_frame",
    "encode_heartbeat",
    "parse_sse_stream",
    "project_public_event",
    "read_bounded_batch",
    "reconnect_from_cursor",
    "resolve_event_cursor",
]
