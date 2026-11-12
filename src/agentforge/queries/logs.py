"""Bounded log slice reads by opaque log id (D12.10)."""

from __future__ import annotations

from pathlib import Path

from agentforge.queries.authorization import AuthenticatedPrincipal


class LogQueryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


MAX_SLICE_BYTES = 64 * 1024


def read_log_slice(
    *,
    log_root: Path,
    log_id: str,
    offset: int,
    limit: int,
    principal: AuthenticatedPrincipal,
) -> dict[str, object]:
    if not principal.credential_id:
        raise LogQueryError("unauthorized", "principal required")
    if not log_id or "/" in log_id or "\\" in log_id or ".." in log_id:
        raise LogQueryError("invalid_request", "illegal log id")
    if offset < 0 or limit < 1:
        raise LogQueryError("invalid_request", "offset/limit invalid")
    limit = min(limit, MAX_SLICE_BYTES)
    path = Path(log_root).resolve() / f"{log_id}.log"
    if not path.is_file():
        raise LogQueryError("not_found", log_id)
    # Ensure path stays under root
    try:
        path.relative_to(Path(log_root).resolve())
    except ValueError as exc:
        raise LogQueryError("forbidden", "log path escape") from exc
    data = path.read_bytes()
    slice_bytes = data[offset : offset + limit]
    return {
        "logId": log_id,
        "offset": offset,
        "limit": limit,
        "bytes": len(slice_bytes),
        "truncated": len(data) > offset + limit,
        "content": slice_bytes.decode("utf-8", errors="replace"),
        "contentType": "text/plain; charset=utf-8",
    }
