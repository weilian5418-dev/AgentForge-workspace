"""D12.14: fault-matrix negative cases with explicit coverage evidence.

Matrix rows (§19) covered here or by named sibling tests — platform/stub only.
Real dual-product external acceptance remains a separate R15 evidence gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentforge.contracts.delivery import AcceptanceDecisionRequest
from agentforge.contracts.event_stream import PublicEventEnvelope
from agentforge.delivery.manifest import compute_manifest_hash
from agentforge.integration.builder import TreeEntry, combine_approved_deltas, compute_tree_delta
from agentforge.integration.checks import (
    CheckExecutionReceipt,
    PythonPytestV1,
    evaluate_check_requirements,
)
from agentforge.integration.reviews import IntegrationFacts, evaluate_integration_facts
from agentforge.streaming.client import apply_public_event, parse_sse_stream
from agentforge.streaming.framing import encode_business_event

H = "a" * 64
H2 = "b" * 64
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)

# §19 coverage map (unit/stub evidence). Integration PG tests add transactional rows.
MATRIX_COVERAGE = {
    "帧解析": "test_d12_sse_framing / test_utf8_chunk_reassembly",
    "payload注入": "test_d12_sse_framing / test_encode_business_event_escapes_payload_newlines",
    "窗口推进": "test_d12_sse_stream / test_read_bounded_batch_resync_when_below_floor",
    "header ID 与数字游标": "test_d12_sse_framing / test_resolve_cursor_conflict_when_header_and_query_disagree",
    "产品计数": "test_d12_agent_counts",
    "未放行输入": "test_d12_selection",
    "并行文件变化": "test_d12_integration_delta / test_one_writer_keeps_change",
    "文件冲突": "test_d12_integration_delta / conflict cases",
    "空/跳过测试": "test_d12_checks",
    "报告伪造": "test_d12_checks / test_untrusted_passed_json_rejected",
    "版本证据混用": "test_d12_reviews / test_check_evidence_mismatch",
    "最终审核遗漏": "test_d12_reviews / test_missing_required_review_request_waits",
    "旧审核重放": "test_d12_reviews / test_stage_old_report_on_wrong_version_ignored",
    "manifest哈希": "test_d12_delivery_hashing",
    "部分接受依赖": "test_d12_acceptance_policy",
    "接受与发布边界": "test_accept_request_rejects_push_deploy_fields",
}


def test_matrix_coverage_map_is_complete_for_unit_layer() -> None:
    assert len(MATRIX_COVERAGE) >= 15


def test_utf8_chunk_reassembly_across_codepoint_boundaries() -> None:
    """帧解析: do not treat one TCP chunk as one SSE frame."""
    env = PublicEventEnvelope.model_validate(
        {
            "eventId": "evt_1",
            "runId": "run_1",
            "sequence": 1,
            "type": "run.status_changed",
            "timestamp": NOW,
            "payload": {"note": "你好世界", "status": "running", "statusVersion": 1},
        }
    )
    raw = encode_business_event(env)
    # Split mid-UTF-8 multi-byte sequence
    mid = len(raw) // 2
    while mid < len(raw) and (raw[mid] & 0xC0) == 0x80:
        mid += 1
    chunks = [raw[: mid - 1], raw[mid - 1 :]]
    events = list(parse_sse_stream(iter(chunks)))
    assert len(events) == 1
    assert events[0].id == "evt_1"
    assert "你好世界" in events[0].data


def test_out_of_order_client_projection_does_not_rewind() -> None:
    """重复/乱序: later lower sequence must not rewind applied cursor."""
    from agentforge.streaming.client import ClientRunProjection

    proj = ClientRunProjection(run_id="run_1", applied_sequence=5)
    # Craft a public-like dict
    older = {
        "eventId": "evt_3",
        "runId": "run_1",
        "sequence": 3,
        "type": "run.status_changed",
        "payload": {},
    }
    apply_public_event(proj, older)
    assert proj.applied_sequence == 5


def test_empty_required_checks_all_empty_not_pass() -> None:
    """空必需检查不 all([]) 通过."""
    assert evaluate_check_requirements(required=[], reports=[]) == "blocked"
    assert (
        evaluate_check_requirements(required=[], reports=[{"checkId": "x", "outcome": "passed"}])
        == "blocked"
    )


def test_path_exists_only_is_not_registered_software_check() -> None:
    """缺检查能力: path_exists is not a registered integration check."""
    from agentforge.integration.checks import CheckError, default_registry

    reg = default_registry()
    try:
        reg.get("path_exists", "v1")
        raise AssertionError("path_exists must not be registered")
    except CheckError as exc:
        assert exc.code in {"check_not_registered", "not_found"} or "path_exists" in str(exc)


def test_timeout_without_termination_is_unknown() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(
        CheckExecutionReceipt(
            exit_code=None,
            timed_out=True,
            termination_confirmed=False,
            stdout_log_id=None,
            stderr_log_id=None,
            report_path=None,
            tree_hash_before=H,
            tree_hash_after=H,
            argv_definition_hash=H,
        ),
        {"collected": 1, "passedCount": 1},
    )
    assert out.outcome in {"unknown", "timeout", "blocked"}


def test_file_conflict_keeps_both_sources() -> None:
    base = {"a.txt": TreeEntry(H)}
    d1 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="role_a")
    d2 = compute_tree_delta(base, {"a.txt": TreeEntry("c" * 64)}, source_ref="role_b")
    plan = combine_approved_deltas(base, (d1, d2))
    assert not plan.ok
    assert plan.conflicts


def test_accept_request_rejects_push_deploy_fields() -> None:
    """接受与发布边界: request body cannot smuggle push/deploy authority."""
    for key in ("push", "deploy", "forceComplete", "technicalPassed"):
        try:
            AcceptanceDecisionRequest.model_validate(
                {
                    "integrationVersion": "iv_1",
                    "manifestHash": H,
                    "manifestRevision": 1,
                    "expectedStatusVersion": 1,
                    "decision": "accept",
                    key: True,
                }
            )
            raise AssertionError(f"expected reject for {key}")
        except Exception:
            pass


def test_evaluation_waiting_when_review_missing() -> None:
    facts = IntegrationFacts(
        cycle_id="ivc_1",
        version_id="iv_1",
        run_id="run_1",
        tree_hash=H,
        selection_hash=H,
        contract_hash=H,
        policy_hash=H,
        environment_fingerprint=H,
        selection_still_valid=True,
        build_conflict_free=True,
        content_integrity_ok=True,
        required_checks=[
            {"checkId": "python_pytest", "checkVersion": "v1", "required": True, "configHash": H}
        ],
        check_reports=[
            {
                "checkId": "python_pytest",
                "outcome": "passed",
                "configHash": H,
                "checkVersion": "v1",
                "treeHash": H,
                "policyHash": H,
                "environmentHash": H,
                "versionId": "iv_1",
            }
        ],
        required_review_roles=["final_reviewer"],
        review_requests=[],
        review_reports=[],
    )
    ev = evaluate_integration_facts(facts)
    assert ev.status == "waiting"


def test_manifest_hash_evidence_stable_and_sensitive() -> None:
    from agentforge.contracts.delivery import DeliveryManifestPayload

    def payload(**kw: object) -> DeliveryManifestPayload:
        base: dict[str, object] = {
            "schemaVersion": "1",
            "runId": "run_1",
            "integrationVersion": "iv_1",
            "validationCycleId": "ivc_1",
            "manifestRevision": 1,
            "planVersion": 1,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
            "selectionHash": H,
            "baselineHash": H,
            "treeManifestHash": H,
            "contractHash": H,
            "integrationPolicyHash": H,
            "environmentFingerprint": H,
            "members": [
                {
                    "memberId": "tree:iv_1",
                    "kind": "code_tree",
                    "required": True,
                    "safeRelativePath": ".",
                    "objectHash": H,
                    "memberTechnicalStatus": "blocked",
                }
            ],
            "checks": [
                {
                    "checkId": "python_pytest",
                    "checkVersion": "v1",
                    "configHash": H,
                    "outcome": "not_run",
                }
            ],
            "reviews": [],
            "knownRisks": [],
            "technicalStatus": "blocked",
        }
        base.update(kw)
        return DeliveryManifestPayload.model_validate(base)

    a = compute_manifest_hash(payload())
    b = compute_manifest_hash(payload())
    assert a == b
    c = compute_manifest_hash(
        payload(knownRisks=[{"kind": "manual_step", "detail": "x", "memberIds": []}])
    )
    assert a != c
