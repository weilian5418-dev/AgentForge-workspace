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
from agentforge.streaming.retention import (
    advance_replay_floor,
    archive_event_range,
    get_replay_window,
    read_after_in_window,
)
from agentforge.streaming.service import EventStreamService, read_bounded_batch

__all__ = [
    "ClientRunProjection",
    "CursorError",
    "EventStreamService",
    "ProjectionError",
    "advance_replay_floor",
    "archive_event_range",
    "encode_business_event",
    "encode_control_frame",
    "encode_heartbeat",
    "get_replay_window",
    "parse_sse_stream",
    "project_public_event",
    "read_after_in_window",
    "read_bounded_batch",
    "reconnect_from_cursor",
    "resolve_event_cursor",
]
