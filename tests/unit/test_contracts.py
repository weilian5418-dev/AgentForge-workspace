from datetime import datetime

import pytest
from pydantic import ValidationError

from agentforge.contracts.artifacts import ArtifactContract, ArtifactRef, WorkPackage
from agentforge.contracts.base import ApiInputModel
from agentforge.contracts.common import BudgetLimits, ensure_utc
from agentforge.contracts.errors import ErrorCode, build_error, sanitize_error_details
from agentforge.contracts.events import EventEnvelope, parse_event_payload
from agentforge.contracts.plans import ApprovalSummary, PlanDecisionRequest
from agentforge.contracts.runs import RunCreateRequest
from agentforge.contracts.templates import TemplateDocument
from agentforge.contracts.validations import ValidationReport


def sample_artifact() -> dict:
    return {
        "id": "release",
        "kind": "markdown",
        "relativePath": "outputs/notes.md",
        "required": True,
        "criteria": [{"id": "sections", "description": "Required sections", "required": True}],
        "validators": [
            {
                "id": "markdown",
                "version": "1",
                "criterionIds": ["sections"],
                "required": True,
                "config": {},
            }
        ],
        "independentReview": False,
    }


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "C:/secret",
        r"C:secret",
        "/etc/passwd",
        r"\\server\share",
        r"a\..\secret",
        "a:stream",
        ".",
    ],
)
def test_artifact_rejects_out_of_scope_paths(path: str) -> None:
    data = sample_artifact()
    data["relativePath"] = path
    with pytest.raises(ValidationError):
        ArtifactContract.model_validate(data)


def test_validator_cannot_reference_missing_criteria() -> None:
    data = sample_artifact()
    data["validators"][0]["criterionIds"] = ["missing"]
    with pytest.raises(ValidationError):
        ArtifactContract.model_validate(data)


def test_alias_roundtrip_and_unknown_fields() -> None:
    data = sample_artifact()
    assert ArtifactContract.model_validate(data).model_dump() == data
    data["internalState"] = "published"
    with pytest.raises(ValidationError):
        ArtifactContract.model_validate(data)


def test_strict_versions() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate({"artifactId": "x", "version": "1", "sha256": "a" * 64})


def test_work_package_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError):
        WorkPackage.model_validate(
            {
                "id": "a",
                "planVersion": 1,
                "stageId": "implementation",
                "objective": "test",
                "ownerRoleId": "writer",
                "dependsOn": ["a"],
                "inputs": [],
                "deliverables": [],
                "readScopes": [],
                "writeScopes": [],
                "toolPolicyId": "restricted",
                "maxRepairRounds": 3,
            }
        )


def test_api_input_rejects_snake_case_json() -> None:
    with pytest.raises(ValidationError):
        RunCreateRequest.model_validate(
            {
                "project_id": "project_demo",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {"maxTokens": 1000},
                "planning_provider_profile_id": "profile_local",
            }
        )


def test_api_input_accepts_documented_aliases() -> None:
    model = RunCreateRequest.model_validate(
        {
            "projectId": "project_demo",
            "goal": "ship notes",
            "outputs": ["markdown"],
            "budget": {"maxTokens": 1000},
            "planningProviderProfileId": "profile_local",
        }
    )
    assert model.project_id == "project_demo"
    assert model.planning_provider_profile_id == "profile_local"
    assert model.model_dump()["projectId"] == "project_demo"
    assert model.model_dump()["planningProviderProfileId"] == "profile_local"


def test_run_create_accepts_deprecated_provider_profile_alias() -> None:
    model = RunCreateRequest.model_validate(
        {
            "projectId": "project_demo",
            "goal": "ship notes",
            "outputs": ["markdown"],
            "budget": {"maxTokens": 1000},
            "providerProfileId": "profile_legacy",
        }
    )
    assert model.planning_provider_profile_id == "profile_legacy"


def test_nested_budget_rejects_snake_case_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunCreateRequest.model_validate(
            {
                "projectId": "project_demo",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {"max_tokens": 100},
                "planningProviderProfileId": "profile_local",
            }
        )
    with pytest.raises(ValidationError):
        RunCreateRequest.model_validate(
            {
                "projectId": "project_demo",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {"maxTokens": 100, "max_tokens": 200},
                "planningProviderProfileId": "profile_local",
            }
        )
    with pytest.raises(ValidationError):
        RunCreateRequest.model_validate(
            {
                "projectId": "project_demo",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {"maxTokens": 100, "extraField": 1},
                "planningProviderProfileId": "profile_local",
            }
        )


def test_budget_rejects_string_numbers() -> None:
    with pytest.raises(ValidationError):
        BudgetLimits.model_validate({"maxTokens": "1000"})


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 11, 10, 12, 0, 0))


def test_error_details_are_sanitized() -> None:
    cleaned = sanitize_error_details(
        {
            "token": "secret-value",
            "path": "/ok/path",
            "databaseUrl": "postgresql+psycopg://x",
            "note": "safe",
        }
    )
    assert cleaned == {"path": "/ok/path", "note": "safe"}
    envelope = build_error(
        code=ErrorCode.UNAUTHORIZED,
        message="Authentication required",
        request_id="req_1",
        details={"authorization": "Bearer abc", "field": "goal"},
    )
    assert envelope.error.details == {"field": "goal"}
    assert envelope.error.retryable is False


def test_passed_validation_cannot_skip_evidence() -> None:
    payload = {
        "id": "val_1",
        "artifact": {"artifactId": "a1", "version": 1, "sha256": "a" * 64},
        "contractId": "c1",
        "contractVersion": 1,
        "validatorId": "markdown",
        "validatorVersion": "1",
        "status": "passed",
        "criterionResults": [
            {
                "criterionId": "sections",
                "status": "passed",
                "evidenceRefs": [],
                "message": "ok",
            }
        ],
        "environmentRef": "env_1",
        "startedAt": "2026-11-10T04:00:00Z",
        "finishedAt": "2026-11-10T04:00:01Z",
        "evidenceRefs": [],
    }
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(payload)


def test_event_payload_is_typed() -> None:
    payload = parse_event_payload(
        "run.status_changed",
        {"status": "running", "statusVersion": 2, "reason": "approved"},
    )
    envelope = EventEnvelope.model_validate(
        {
            "schemaVersion": "1",
            "eventId": "evt_1",
            "runId": "run_1",
            "sequence": 1,
            "type": "run.status_changed",
            "timestamp": "2026-11-10T04:00:00Z",
            "payload": payload.model_dump(mode="json"),
        }
    )
    assert envelope.sequence == 1
    assert envelope.model_dump()["eventId"] == "evt_1"


def test_api_input_model_is_strict_alias_subclass() -> None:
    assert issubclass(RunCreateRequest, ApiInputModel)


def test_plan_decision_requires_assignment_and_gate_versions() -> None:
    with pytest.raises(ValidationError):
        PlanDecisionRequest.model_validate(
            {
                "decision": "approve",
                "planVersion": 1,
                "planHash": "a" * 64,
            }
        )
    model = PlanDecisionRequest.model_validate(
        {
            "decision": "approve",
            "planVersion": 1,
            "planHash": "a" * 64,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
        }
    )
    assert model.assignment_version == 1
    assert model.gate_policy_version == 1


def test_approval_summary_approved_is_not_native_confirmed() -> None:
    summary = ApprovalSummary.model_validate(
        {
            "id": "appr_1",
            "action": "shell",
            "targetSummary": "npm test",
            "parameterSummary": "{}",
            "actionDigest": "b" * 64,
            "expiresAt": "2026-11-10T05:00:00Z",
            "status": "approved",
            "userDecision": "approve",
            "nativeConfirmed": False,
        }
    )
    assert summary.status == "approved"
    assert summary.native_confirmed is False


def test_template_v2_requires_roles_and_stages() -> None:
    with pytest.raises(ValidationError):
        TemplateDocument.model_validate(
            {
                "schemaVersion": "1",
                "name": "legacy",
                "workPackages": [{"id": "wp1", "role": "dev", "objective": "x"}],
            }
        )
    doc = TemplateDocument.model_validate(
        {
            "schemaVersion": "2",
            "name": "software-collaboration",
            "roles": [
                {
                    "id": "developer",
                    "requiredCapabilities": ["createSession", "workspaceBinding"],
                }
            ],
            "stages": [{"id": "implementation", "gatePolicy": "stage_atomic"}],
            "workPackages": [
                {
                    "id": "implement",
                    "stageId": "implementation",
                    "ownerRoleId": "developer",
                    "objective": "ship change",
                }
            ],
        }
    )
    assert doc.schema_version == "2"
    assert doc.assignments_required is True


def test_v14_collaboration_events_parse() -> None:
    payload = parse_event_payload(
        "stage.released",
        {
            "stageId": "implementation",
            "candidateId": "cand_1",
            "manifestHash": "c" * 64,
            "gateResult": "released",
        },
    )
    assert payload.model_dump()["gateResult"] == "released"


def test_public_message_rejects_stage_release_and_requires_typed_payload() -> None:
    from agentforge.contracts.messages import MessageCreateRequest

    with pytest.raises(ValidationError):
        MessageCreateRequest.model_validate(
            {
                "recipientRefs": ["inst_1"],
                "payload": {
                    "type": "stage.release",
                    "stageId": "s1",
                    "gateDecisionId": "g1",
                    "candidateId": "c1",
                    "manifestHash": "a" * 64,
                },
            }
        )
    with pytest.raises(ValidationError):
        MessageCreateRequest.model_validate(
            {
                "recipientRefs": ["inst_1"],
                "payload": {"type": "task.assignment", "unrelated": True},
            }
        )
    ok = MessageCreateRequest.model_validate(
        {
            "recipientRefs": ["inst_1"],
            "payload": {
                "type": "task.assignment",
                "workPackageId": "wp_1",
                "objective": "implement feature",
            },
        }
    )
    assert ok.type == "task.assignment"
    assert ok.payload.work_package_id == "wp_1"


def _assert_pydantic_and_schema_agree(payload: dict, *, accept: bool) -> None:
    import jsonschema

    from agentforge.contracts.messages import MessageCreateRequest

    schema = MessageCreateRequest.model_json_schema(by_alias=True)
    body = {"recipientRefs": ["inst_1"], "payload": payload}
    if accept:
        MessageCreateRequest.model_validate(body)
        jsonschema.validate(instance=body, schema=schema)
    else:
        with pytest.raises(ValidationError):
            MessageCreateRequest.model_validate(body)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=body, schema=schema)


def test_message_create_schema_and_runtime_agree_on_counterexamples() -> None:
    """Dual validation: discriminator required; camelCase only; no unrelated fields."""
    _assert_pydantic_and_schema_agree(
        {"type": "task.assignment", "workPackageId": "wp_1", "objective": "ship"},
        accept=True,
    )
    # Missing discriminator key
    _assert_pydantic_and_schema_agree(
        {"workPackageId": "wp_1", "objective": "ship"},
        accept=False,
    )
    # Nested snake_case rejected by both (ApiInputModel + Schema aliases)
    _assert_pydantic_and_schema_agree(
        {
            "type": "task.assignment",
            "work_package_id": "wp_1",
            "objective": "ship",
        },
        accept=False,
    )
    # Mixed alias
    _assert_pydantic_and_schema_agree(
        {
            "type": "task.assignment",
            "workPackageId": "wp_1",
            "work_package_id": "wp_2",
            "objective": "ship",
        },
        accept=False,
    )
    _assert_pydantic_and_schema_agree(
        {"type": "task.assignment", "unrelated": True},
        accept=False,
    )
    _assert_pydantic_and_schema_agree(
        {
            "type": "stage.release",
            "stageId": "s1",
            "gateDecisionId": "g1",
            "candidateId": "c1",
            "manifestHash": "a" * 64,
        },
        accept=False,
    )
    _assert_pydantic_and_schema_agree(
        {
            "type": "clarification.request",
            "question": "Which API shape?",
        },
        accept=True,
    )
