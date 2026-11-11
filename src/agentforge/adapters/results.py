"""Shared adapter result helpers — unsupported is a normal outcome."""

from __future__ import annotations

from typing import Any, Literal

Outcome = Literal["supported", "unsupported", "unknown", "failed"]


def outcome(
    status: Outcome,
    *,
    detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status}
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    return payload


def unsupported(detail: str) -> dict[str, Any]:
    return outcome("unsupported", detail=detail)


def unknown(detail: str) -> dict[str, Any]:
    return outcome("unknown", detail=detail)


def failed(detail: str) -> dict[str, Any]:
    return outcome("failed", detail=detail)
