"""Queue entry points for stage stopping, fixed candidates, validation and release."""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select

from agentforge.artifacts.service import ArtifactService
from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.artifact_bus import FreezeCandidateRequest, VerifyCandidateRequest
from agentforge.contracts.plans import PlanSpec
from agentforge.instances.context_loader import ExecutionContextLoader
from agentforge.instances.worker import process_instance_operation
from agentforge.stages.service import StageService
from agentforge.storage.models import (
    AgentInstanceRow,
    ArtifactCandidateRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    Plan,
    ReviewCycleRow,
    ReviewRequestRow,
    Run,
    StageRow,
    StageSubmissionRow,
)
from agentforge.storage.required import required
from agentforge.storage.uow import unit_of_work_connection


def process_submission(engine: Any, settings: Any, submission_id: Any) -> Any:
    store = ArtifactStore(settings.state_dir / "artifact_store")
    with unit_of_work_connection(engine) as uow:
        sub = required(uow.session, StageSubmissionRow, submission_id)
        if sub is None:
            raise ValueError("Unknown submission")
        stage = required(uow.session, StageRow, sub.stage_id)
        prior_cycle = (
            uow.session.get(ReviewCycleRow, stage.current_review_cycle_id)
            if stage.current_review_cycle_id
            else None
        )
        if sub.manifest_hash and prior_cycle and prior_cycle.status == "decided":
            return {"status": "succeeded"}
        operation_ids = []
        for ref in sub.attempt_refs:
            attempt = required(uow.session, InstanceAttemptRow, ref)
            instance = required(uow.session, AgentInstanceRow, attempt.instance_id)
            key = f"submission-stop:{sub.id}:{ref}"
            op = uow.session.scalar(
                select(InstanceOperationRow).where(InstanceOperationRow.idempotency_key == key)
            )
            if op is None:
                op = InstanceOperationRow(
                    id="iop_" + uuid.uuid4().hex,
                    instance_id=instance.id,
                    attempt_id=ref,
                    kind="request_stop",
                    status="prepared",
                    idempotency_key=key,
                    payload_json={
                        "sessionRef": instance.session_ref,
                        "nativeSessionId": instance.native_session_id,
                        "generation": attempt.generation,
                    },
                )
                uow.session.add(op)
            operation_ids.append(op.id)
    for operation in operation_ids:
        result = process_instance_operation(engine, operation)
        if result["status"] != "succeeded":
            return {"status": "blocked", "reason": "stop_unconfirmed"}
    with unit_of_work_connection(engine) as uow:
        s = uow.session
        sub = required(s, StageSubmissionRow, submission_id)
        stage = required(s, StageRow, sub.stage_id)
        run = required(s, Run, stage.run_id)
        plan = required(s, Plan, (run.id, stage.plan_version))
        spec = PlanSpec.model_validate(plan.spec_json)
        for ref, opid in zip(sub.attempt_refs, operation_ids, strict=True):
            attempt = required(s, InstanceAttemptRow, ref)
            instance = required(s, AgentInstanceRow, attempt.instance_id)
            op = required(s, InstanceOperationRow, opid)
            proof = op.payload_json["stopProof"]
            digest = hashlib.sha256(
                json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            ctx = ExecutionContextLoader(s).load(
                instance_id=instance.id, operation_id=opid, require_session=True
            )
            package = next(w for w in spec.work_packages if w.id == attempt.work_package_id)
            for output in package.deliverables:
                existing = s.scalar(
                    select(ArtifactCandidateRow).where(
                        ArtifactCandidateRow.attempt_id == ref,
                        ArtifactCandidateRow.contract_id == output.id,
                    )
                )
                if existing:
                    continue
                candidate = ArtifactService(s, store).freeze_candidate(
                    FreezeCandidateRequest.model_validate(
                        {
                            "projectId": run.project_id,
                            "workspaceId": ctx.workspace_id,
                            "workspaceRoot": ctx.workspace_root,
                            "relativePath": output.relative_path,
                            "contractId": output.id,
                            "contractVersion": 1,
                            "kind": output.kind,
                            "environmentHash": hashlib.sha256(
                                ctx.environment_id.encode()
                            ).hexdigest(),
                            "stopEvidence": {
                                "source": "adapter_host",
                                "operationId": opid,
                                "workspaceId": ctx.workspace_id,
                                "attemptId": ref,
                                "generation": attempt.generation,
                                "evidenceSha256": digest,
                                "writableScope": package.write_scopes[0]
                                if package.write_scopes
                                else ".",
                                "writesQuiesced": True,
                                "checkedAt": proof["checkedAt"],
                            },
                        }
                    )
                )
                for validator in output.validators:
                    # Registry errors are blocked, never replaced with a weaker validator.
                    ArtifactService(s, store).verify(
                        VerifyCandidateRequest.model_validate(
                            {"candidateId": candidate.candidate_id, "validatorId": validator.id}
                        )
                    )
        cycle = StageService(s, store=store).complete_stop_and_freeze(sub.id)
        # Semantic review requests remain explicit structured tasks. D09 transports them.
        for req in s.scalars(
            select(ReviewRequestRow).where(
                ReviewRequestRow.review_cycle_id == cycle.review_cycle_id
            )
        ).all():
            from agentforge.contracts.messages import MessageCreateRequest
            from agentforge.messaging.service import MessageService

            for reviewer in req.legal_responder_refs:
                MessageService(s).create(
                    run.id,
                    MessageCreateRequest.model_validate(
                        {
                            "idempotencyKey": req.id,
                            "recipientRefs": [reviewer],
                            "stageId": stage.id,
                            "replyRequired": True,
                            "requestId": req.id,
                            "payload": {
                                "type": "clarification.request",
                                "question": "Review the fixed candidate set. Return a signed structured ReviewReport for request "
                                + req.id,
                                "contextRefs": [cycle.candidate_set_id],
                            },
                        }
                    ),
                    sender_ref="platform:review",
                )
        result = StageService(s, store=store).apply_evaluation(stage.id)
        return {"status": result.get("status", "released")}
