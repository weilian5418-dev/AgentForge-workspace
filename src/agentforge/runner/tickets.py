"""Runner execution tickets and actionDigest (D05)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_runner_digest(
    *,
    profile_id: str,
    workspace_id: str,
    node_id: str,
    attempt_id: str | None,
    generation: int,
    scope: str,
    params: dict[str, Any],
) -> str:
    payload = {
        "attemptId": attempt_id or "",
        "generation": generation,
        "nodeId": node_id,
        "params": params,
        "profileId": profile_id,
        "scope": scope,
        "workspaceId": workspace_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
