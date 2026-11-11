"""Canonical actionDigest for approval / operation authorization (D04)."""

from __future__ import annotations

from typing import Any

from agentforge.permissions.action import ActionDescriptor, action_descriptor_digest


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
    operation_type: str = "tool.invoke",
    workspace_id: str | None = None,
    workspace_root: str = "",
    params: dict[str, Any] | None = None,
    environment_id: str | None = None,
    generation: int | None = None,
    instance_id: str | None = None,
) -> str:
    """Digest binds the *final action* (ActionDescriptor), not native request ids.

    ``native_request_id`` / ``semantic`` remain on the ledger row but are not part of
    the execution bind key — Runner and approval must share this digest.
    """
    _ = (native_request_id, semantic)  # ledger fields; not in execution digest
    descriptor = ActionDescriptor(
        operation_type=operation_type,
        params=params
        if params is not None
        else {
            "action": action,
            "target": target_summary,
            "parameters": parameter_summary,
        },
        workspace_id=workspace_id or requested_scope,
        workspace_root=workspace_root,
        scope=requested_scope,
        environment_id=environment_id,
        attempt_id=attempt_id,
        generation=generation,
        instance_id=instance_id,
        session_ref=session_ref,
    )
    return action_descriptor_digest(descriptor)
