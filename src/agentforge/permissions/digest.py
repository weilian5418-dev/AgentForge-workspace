"""Canonical actionDigest for approval / operation authorization (D04)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_action_digest(
    *,
    action: str,
    target_summary: str,
    parameter_summary: str,
    requested_scope: str,
    session_ref: str,
    native_request_id: str,
    attempt_id: str | None,
    semantic: str,
) -> str:
    """Stable SHA-256 over a sorted canonical object (no floating auth expansion)."""
    payload: dict[str, Any] = {
        "action": action,
        "attemptId": attempt_id or "",
        "nativeRequestId": native_request_id,
        "parameterSummary": parameter_summary,
        "requestedScope": requested_scope,
        "semantic": semantic,
        "sessionRef": session_ref,
        "targetSummary": target_summary,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
