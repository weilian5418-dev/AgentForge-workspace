"""Persist the next integration phase; a queue receipt is never validation evidence."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentforge.core.config import Settings
from agentforge.storage.models import (
    AgentInstanceRow,
    IntegrationValidationCycleRow,
    IntegrationVersionRow,
    Outbox,
    Run,
)
from agentforge.storage.uow import unit_of_work_connection


def advance_integration(engine: Engine, settings: Settings, *, cycle_id: str) -> str:
    from agentforge.contracts.messages import MessageCreateRequest
    from agentforge.instances.bindings import require_profile
    from agentforge.integration.reviews import open_final_review
    from agentforge.integration.service import evaluate_integration_cycle
    from agentforge.messaging.service import MessageService

    with unit_of_work_connection(engine) as uow:
        s = uow.session
        cycle = s.get(IntegrationValidationCycleRow, cycle_id)
        if cycle is None:
            raise ValueError("cycle_missing")
        version = s.get(IntegrationVersionRow, cycle.version_id)
        assert version is not None
        run = s.get(Run, version.run_id, with_for_update=True)
        assert run is not None
        for role in cycle.required_review_roles:
            instances = list(
                s.scalars(
                    select(AgentInstanceRow).where(
                        AgentInstanceRow.run_id == run.id,
                        AgentInstanceRow.plan_version == run.plan_version,
                        AgentInstanceRow.assignment_version == run.assignment_version,
                        AgentInstanceRow.role_id == role,
                    )
                ).all()
            )
            if len(instances) != 1 or instances[0].lifecycle != "active":
                cycle.status = "blocked"
                return "reviewer_not_available"
            instance = instances[0]
            # Read-only review needs actual versioned evidence, not a prompt promise.
            require_profile(s, instance.agent_profile_id, {"readOnlyReview"})
            request = open_final_review(s, cycle_id=cycle_id, reviewer_assignment_ref=instance.id)
            if request.status == "pending":
                MessageService(s).create(
                    run.id,
                    MessageCreateRequest.model_validate(
                        {
                            "idempotencyKey": f"final-review:{request.request_id}",
                            "recipientRefs": [instance.id],
                            "requestId": request.request_id,
                            "replyRequired": True,
                            "payload": {
                                "type": "clarification.request",
                                "question": "Review this immutable integration tree and submit the bound structured response.",
                                "contextRefs": [version.id],
                            },
                        }
                    ),
                    sender_ref="platform:final-review",
                )
        evaluation = evaluate_integration_cycle(s, cycle_id=cycle_id)
        if evaluation.status == "passed":
            key = f"manifest:{cycle_id}"
            if not s.scalar(select(Outbox).where(Outbox.idempotency_key == key)):
                s.add(
                    Outbox(
                        id="obx_" + uuid.uuid4().hex,
                        run_id=run.id,
                        intent_type="delivery.manifest_build",
                        target="delivery",
                        payload={
                            "operationId": cycle.id,
                            "cycleId": cycle.id,
                            "versionId": version.id,
                        },
                        idempotency_key=key,
                        delivery_status="pending",
                        attempt_count=0,
                    )
                )
        return evaluation.status
