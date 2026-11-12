"""Bridge verified structured messages into StageService.submit (D11.7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.stages import StageSubmissionRequest
from agentforge.instances.errors import InstanceError
from agentforge.stages.errors import StageError
from agentforge.stages.service import StageService
from agentforge.storage.models import (
    AgentInstanceRow,
    CollaborationMessageRow,
    InstanceAttemptRow,
    Run,
)


class SubmissionBridge:
    """Only structured, bound stage.submission messages may call StageService.submit.

    Natural-language done / fact.turn_finished / idle never invent a submission.
    """

    def __init__(self, session: Session, *, stage_service: StageService | None = None) -> None:
        self.session = session
        self.stages = stage_service or StageService(session)

    def accept(self, message_or_receipt_ref: str) -> dict[str, Any]:
        message = self._load_message(message_or_receipt_ref)
        if message.message_type != "stage.submission":
            raise InstanceError(
                "submission_rejected",
                f"message type {message.message_type} is not a structured submission",
            )

        payload = message.payload or {}
        stage_id = message.stage_id or payload.get("stageId") or payload.get("stage_id")
        candidate_ref = payload.get("candidateRef") or payload.get("candidate_ref")
        attempt_refs = payload.get("attemptRefs") or payload.get("attempt_refs") or []
        if not stage_id or not attempt_refs:
            raise InstanceError("submission_rejected", "stage and attempt refs required")

        run = self.session.get(Run, message.run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise InstanceError("run_not_found", message.run_id)
        if run.status not in {"preparing", "running", "pause_requested"}:
            raise InstanceError("execution_blocked", f"run status {run.status}")

        attempts = [self.session.get(InstanceAttemptRow, ref) for ref in attempt_refs]
        if any(a is None for a in attempts):
            raise InstanceError("submission_rejected", "attempt missing")
        assert all(a is not None for a in attempts)

        sender = message.sender_ref
        for attempt in attempts:
            assert attempt is not None
            if attempt.run_id != run.id:
                raise InstanceError("submission_rejected", "attempt wrong run")
            if attempt.generation != run.active_generation:
                raise InstanceError("submission_rejected", "stale generation")
            if attempt.stage_id and attempt.stage_id != stage_id:
                raise InstanceError("submission_rejected", "attempt stage mismatch")
            if attempt.status not in {
                "dispatching",
                "running",
                "submitted",
                "awaiting_stop_verification",
                "unknown",
            }:
                raise InstanceError(
                    "submission_rejected",
                    f"attempt status {attempt.status} cannot submit",
                )
            instance = self.session.get(AgentInstanceRow, attempt.instance_id)
            if instance is None:
                raise InstanceError("instance_not_found", attempt.instance_id)
            if instance.native_session_id is None:
                raise InstanceError("submission_rejected", "instance has no session")
            if not self._sender_authorized(sender, instance=instance, attempt=attempt):
                raise InstanceError("illegal_submitter", "sender does not own attempt")

        work_package_ids = sorted({a.work_package_id for a in attempts if a is not None})
        body = StageSubmissionRequest.model_validate(
            {
                "workPackageIds": work_package_ids,
                "attemptRefs": list(attempt_refs),
                "candidateRef": candidate_ref,
                "note": f"bridged:{message.id}",
                "idempotencyKey": f"msg:{message.id}",
            }
        )
        try:
            accepted = self.stages.submit(stage_id, body, submitter_ref=sender)
        except StageError as exc:
            raise InstanceError(exc.code, exc.message) from exc

        for attempt in attempts:
            assert attempt is not None
            if attempt.status in {"dispatching", "running", "unknown"}:
                attempt.status = "submitted"
                attempt.submission_id = accepted.operation_id

        return {
            "status": accepted.status,
            "operationId": accepted.operation_id,
            "messageId": message.id,
            "stageId": stage_id,
        }

    def _load_message(self, ref: str) -> CollaborationMessageRow:
        message = self.session.get(CollaborationMessageRow, ref)
        if message is not None:
            return message
        # Allow receipt-style refs that embed the message id.
        if ":" in ref:
            maybe = ref.split(":")[-1]
            message = self.session.get(CollaborationMessageRow, maybe)
            if message is not None:
                return message
        raise InstanceError("message_not_found", ref)

    @staticmethod
    def _sender_authorized(
        sender: str,
        *,
        instance: AgentInstanceRow,
        attempt: InstanceAttemptRow,
    ) -> bool:
        if sender.startswith("user:"):
            return True
        if sender.startswith("platform:"):
            return False
        if sender == instance.id:
            return True
        if sender == attempt.instance_id:
            return True
        return False
