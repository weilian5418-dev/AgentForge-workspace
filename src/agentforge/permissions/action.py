"""Typed ActionDescriptor — shared authorization summary for approvals and Runner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActionDescriptor:
    """Final action to authorize. Digest is the bind key across ledger and execute."""

    operation_type: str
    params: dict[str, Any]
    workspace_id: str
    workspace_root: str
    scope: str
    environment_id: str | None = None
    attempt_id: str | None = None
    generation: int | None = None
    instance_id: str | None = None
    session_ref: str | None = None

    def normalized(self) -> ActionDescriptor:
        root = ""
        if self.workspace_root:
            try:
                root = str(Path(self.workspace_root).resolve())
            except OSError:
                root = self.workspace_root.replace("\\", "/")
        return ActionDescriptor(
            operation_type=self.operation_type,
            params=dict(self.params),
            workspace_id=self.workspace_id,
            workspace_root=root,
            scope=self.scope,
            environment_id=self.environment_id,
            attempt_id=self.attempt_id,
            generation=self.generation,
            instance_id=self.instance_id,
            session_ref=self.session_ref,
        )


def action_descriptor_digest(descriptor: ActionDescriptor) -> str:
    d = descriptor.normalized()
    payload: dict[str, Any] = {
        "attemptId": d.attempt_id or "",
        "environmentId": d.environment_id or "",
        "generation": d.generation if d.generation is not None else "",
        "instanceId": d.instance_id or "",
        "operationType": d.operation_type,
        "params": d.params,
        "scope": d.scope,
        "sessionRef": d.session_ref or "",
        "workspaceId": d.workspace_id,
        "workspaceRoot": d.workspace_root,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def descriptor_from_runner(
    *,
    profile_id: str,
    workspace_id: str,
    workspace_root: str,
    scope: str,
    params: dict[str, Any],
    attempt_id: str | None,
    generation: int,
    instance_id: str | None = None,
    session_ref: str | None = None,
    environment_id: str | None = None,
) -> ActionDescriptor:
    return ActionDescriptor(
        operation_type=f"runner:{profile_id}",
        params=dict(params),
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        scope=scope,
        environment_id=environment_id,
        attempt_id=attempt_id,
        generation=generation,
        instance_id=instance_id,
        session_ref=session_ref,
    ).normalized()
