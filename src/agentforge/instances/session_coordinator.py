"""Session creation with crash-window handling (succeeded but not recorded)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from agentforge.instances.context_loader import ExecutionContextLoader
from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.instances.status import combine_instance_status_facets
from agentforge.storage.models import AgentProfileRow, NativeSessionRow


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SessionAdapter(Protocol):
    def create_session(
        self, *, profile_id: str, workspace_id: str, operation_id: str
    ) -> dict[str, Any]: ...

    def inspect_execution(self, *, session_ref: str) -> dict[str, Any]: ...


AdapterFactory = Callable[[str], SessionAdapter]


class SessionCoordinator:
    def __init__(
        self,
        session: Session,
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.session = session
        self.repo = InstanceRepository(session)
        self.loader = ExecutionContextLoader(session)
        self.adapter_factory = adapter_factory

    def process_create_claims(
        self, *, owner: str = "session_coordinator", limit: int = 10
    ) -> list[dict[str, Any]]:
        """Claim prepared create_session ops, invoke native outside caller's commit.

        Caller must commit the claim transaction before invoking native adapters
        when using a real DB. For in-memory tests, claim + native run in one session.
        """
        claimed = self.repo.claim_operations(
            kinds=("create_session",), owner=owner, limit=limit
        )
        results: list[dict[str, Any]] = []
        for op in claimed:
            results.append(self._complete_create_session(op.id, owner=owner, token=op.claim_token or ""))
        return results

    def complete_create_session_after_commit(
        self, operation_id: str, *, owner: str, token: str
    ) -> dict[str, Any]:
        """Invoke after claim is committed — native call stays outside the claim txn."""
        return self._complete_create_session(operation_id, owner=owner, token=token)

    def _complete_create_session(
        self, operation_id: str, *, owner: str, token: str
    ) -> dict[str, Any]:
        op = self.repo.get_operation(operation_id)
        if op is None:
            raise InstanceError("operation_not_found", operation_id)
        if op.claim_owner != owner or op.claim_token != token:
            raise InstanceError("claim_mismatch", "Operation claim token mismatch")
        if op.kind != "create_session":
            raise InstanceError("wrong_operation_kind", op.kind)

        # Idempotent: already recorded for this operation?
        existing = self.repo.session_by_operation(operation_id)
        if existing is not None:
            op.status = "succeeded"
            op.native_ref = existing.session_ref
            op.completed_at = _now()
            op.claim_owner = None
            op.claim_token = None
            op.claim_expires_at = None
            return {"operationId": operation_id, "status": "succeeded", "replay": True}

        instance = self.repo.get_instance(op.instance_id)
        if instance is None:
            raise InstanceError("instance_not_found", op.instance_id)
        active = self.repo.active_session_for_instance(instance.id)
        if active is not None and active.create_operation_id != operation_id:
            raise InstanceError(
                "active_session_exists",
                "Cannot open another session while one is active",
            )

        profile = self.session.get(AgentProfileRow, instance.agent_profile_id)
        if profile is None:
            raise InstanceError("profile_not_found", instance.agent_profile_id)

        workspace_id = instance.workspace_id or f"ws_pending_{instance.id}"
        if self.adapter_factory is None:
            op.status = "needs_reconciliation"
            op.error_code = "adapter_unavailable"
            op.completed_at = _now()
            instance.lifecycle = "needs_reconciliation"
            instance.status = combine_instance_status_facets(
                "needs_reconciliation", instance.activity or "idle"
            )
            return {"operationId": operation_id, "status": "needs_reconciliation"}

        adapter = self.adapter_factory(profile.adapter_id)
        try:
            # Native call — must not be wrapped in a longer business transaction by callers.
            native = adapter.create_session(
                profile_id=profile.id,
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
        except Exception as exc:  # noqa: BLE001 — persist unknown/failed
            op.status = "failed"
            op.error_code = "native_create_failed"
            op.payload_json = {**dict(op.payload_json or {}), "error": str(exc)[:512]}
            op.completed_at = _now()
            op.claim_owner = None
            op.claim_token = None
            op.claim_expires_at = None
            return {"operationId": operation_id, "status": "failed", "error": str(exc)}

        support = native.get("support") or native.get("status")
        session_ref = native.get("sessionRef") or native.get("session_ref")
        if support in ("unknown", "unsupported") or (
            support not in ("supported", "ok", "succeeded", None) and not session_ref
        ):
            # Keep real limits — do not rewrite to success for demos.
            if support == "unknown" or (support is None and not session_ref):
                op.status = "unknown"
                op.error_code = "native_result_unknown"
                instance.lifecycle = "needs_reconciliation"
                instance.status = combine_instance_status_facets(
                    "needs_reconciliation", instance.activity or "idle"
                )
            else:
                op.status = "failed"
                op.error_code = f"native_{support}"
            op.completed_at = _now()
            op.claim_owner = None
            op.claim_token = None
            op.claim_expires_at = None
            op.payload_json = {**dict(op.payload_json or {}), "native": native}
            return {"operationId": operation_id, "status": op.status, "native": native}

        if not session_ref:
            op.status = "needs_reconciliation"
            op.error_code = "missing_session_ref"
            instance.lifecycle = "needs_reconciliation"
            instance.status = combine_instance_status_facets(
                "needs_reconciliation", instance.activity or "idle"
            )
            op.completed_at = _now()
            op.claim_owner = None
            op.claim_token = None
            op.claim_expires_at = None
            return {"operationId": operation_id, "status": "needs_reconciliation"}

        # Verify workspace / identity when adapter returns them.
        returned_workspace = native.get("workspaceId") or native.get("workspace_id")
        if returned_workspace and instance.workspace_id and returned_workspace != instance.workspace_id:
            op.status = "needs_reconciliation"
            op.error_code = "workspace_mismatch"
            instance.lifecycle = "needs_reconciliation"
            instance.status = combine_instance_status_facets(
                "needs_reconciliation", instance.activity or "idle"
            )
            op.completed_at = _now()
            return {"operationId": operation_id, "status": "needs_reconciliation"}

        ns_id = _id("ns")
        row = NativeSessionRow(
            id=ns_id,
            instance_id=instance.id,
            product_id=native.get("productId") or profile.adapter_id,
            profile_id=profile.id,
            config_version=profile.config_version,
            environment_id=instance.execution_environment,
            session_ref=session_ref,
            create_operation_id=operation_id,
            binding_status="active",
            workspace_id=instance.workspace_id or returned_workspace,
            evidence_json={"native": native},
            created_at=_now(),
        )
        try:
            self.repo.add_native_session(row)
            self.session.flush()
        except Exception:
            # Save failed after native success — keep claimed/unknown, do not open another.
            op.status = "needs_reconciliation"
            op.error_code = "persist_failed_after_native_success"
            op.native_ref = session_ref
            instance.lifecycle = "needs_reconciliation"
            instance.status = combine_instance_status_facets(
                "needs_reconciliation", instance.activity or "idle"
            )
            op.completed_at = _now()
            return {
                "operationId": operation_id,
                "status": "needs_reconciliation",
                "sessionRef": session_ref,
            }

        instance.native_session_id = ns_id
        instance.session_ref = session_ref
        instance.lifecycle = "active"
        instance.activity = "idle"
        instance.status = combine_instance_status_facets("active", "idle")
        instance.updated_at = _now()
        op.status = "succeeded"
        op.native_ref = session_ref
        op.completed_at = _now()
        op.claim_owner = None
        op.claim_token = None
        op.claim_expires_at = None
        return {
            "operationId": operation_id,
            "status": "succeeded",
            "nativeSessionId": ns_id,
            "sessionRef": session_ref,
        }
