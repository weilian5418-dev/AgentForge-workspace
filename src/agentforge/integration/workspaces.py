"""Fixed integration workspace paths and identity markers (D12.9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentforge.driver.binding import sha256_hex


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_workspace_root(state_dir: Path, *, run_id: str, build_id: str) -> Path:
    """Deterministic path from build_id — crash recovery reuses the same directory."""
    safe_run = "".join(c if c.isalnum() or c in "-_" else "_" for c in run_id)[:64]
    return Path(state_dir).resolve() / "integration_builds" / safe_run / build_id


def write_build_marker(
    workspace: Path,
    *,
    build_id: str,
    run_id: str,
    selection_id: str,
    selection_hash: str,
    baseline_tree_hash: str,
    claim_token: str,
) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    marker = workspace / ".agentforge_build.json"
    payload = {
        "schemaVersion": "1",
        "buildId": build_id,
        "runId": run_id,
        "selectionId": selection_id,
        "selectionHash": selection_hash,
        "baselineTreeHash": baseline_tree_hash,
        "claimToken": claim_token,
        "bindingHash": sha256_hex(
            {
                "buildId": build_id,
                "runId": run_id,
                "selectionId": selection_id,
                "selectionHash": selection_hash,
                "baselineTreeHash": baseline_tree_hash,
            }
        ),
    }
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(marker)
    return marker


def read_build_marker(workspace: Path) -> dict[str, Any]:
    marker = workspace / ".agentforge_build.json"
    if not marker.is_file():
        raise WorkspaceError("workspace_identity_unknown", "build marker missing")
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError("workspace_identity_unknown", "build marker unreadable") from exc


def verify_workspace_binding(
    workspace: Path,
    *,
    build_id: str,
    run_id: str,
    selection_id: str,
    selection_hash: str,
    claim_token: str | None,
) -> dict[str, Any]:
    meta = read_build_marker(workspace)
    if meta.get("buildId") != build_id or meta.get("runId") != run_id:
        raise WorkspaceError("workspace_identity_unknown", "build/run identity mismatch")
    if meta.get("selectionId") != selection_id or meta.get("selectionHash") != selection_hash:
        raise WorkspaceError("workspace_identity_unknown", "selection binding mismatch")
    if claim_token is not None and meta.get("claimToken") != claim_token:
        raise WorkspaceError("workspace_identity_unknown", "claim token mismatch")
    return meta
