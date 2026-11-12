"""D12.11: final review binding and technical evaluation."""

from __future__ import annotations

from agentforge.contracts.integrations import IntegrationEvaluation
from agentforge.integration.reviews import IntegrationFacts, evaluate_integration_facts

H = "a" * 64
H2 = "b" * 64


def _facts(**kwargs: object) -> IntegrationFacts:
    base: dict[str, object] = dict(
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
            {
                "checkId": "python_pytest",
                "checkVersion": "v1",
                "required": True,
                "configHash": H,
            }
        ],
        check_reports=[
            {
                "checkId": "python_pytest",
                "outcome": "passed",
                "treeHash": H,
                "policyHash": H,
                "environmentHash": H,
                "versionId": "iv_1",
            }
        ],
        required_review_roles=["final_reviewer"],
        review_requests=[
            {
                "requestId": "irr_1",
                "roleId": "final_reviewer",
                "cycleId": "ivc_1",
                "versionId": "iv_1",
                "treeHash": H,
                "status": "answered",
            }
        ],
        review_reports=[
            {
                "requestId": "irr_1",
                "cycleId": "ivc_1",
                "versionId": "iv_1",
                "treeHash": H,
                "verdict": "approve",
            }
        ],
    )
    base.update(kwargs)
    return IntegrationFacts(**base)  # type: ignore[arg-type]


def test_happy_path_passed() -> None:
    ev = evaluate_integration_facts(_facts())
    assert ev.status == "passed"
    assert ev.required_checks_total == 1
    assert ev.required_checks_satisfied == 1
    assert ev.required_reviews_satisfied == 1


def test_empty_required_checks_cannot_pass() -> None:
    ev = evaluate_integration_facts(
        _facts(required_checks=[], check_reports=[])
    )
    assert ev.status == "blocked"
    assert "required_check_not_passed" in ev.blocker_codes
    # Contract also forbids constructing passed with zero checks
    try:
        IntegrationEvaluation.model_validate(
            {
                "cycleId": "ivc_1",
                "versionId": "iv_1",
                "status": "passed",
                "blockerCodes": [],
                "requiredChecksTotal": 0,
                "requiredChecksSatisfied": 0,
                "requiredReviewsTotal": 0,
                "requiredReviewsSatisfied": 0,
            }
        )
        raise AssertionError("expected validation error")
    except Exception:
        pass


def test_missing_required_review_request_waits() -> None:
    ev = evaluate_integration_facts(
        _facts(review_requests=[], review_reports=[])
    )
    assert ev.status == "waiting"
    assert "review_binding_mismatch" in ev.blocker_codes
    assert ev.required_reviews_satisfied == 0


def test_stage_old_report_on_wrong_version_ignored() -> None:
    """Old cycle/version approve cannot release a new integration version."""
    ev = evaluate_integration_facts(
        _facts(
            review_reports=[
                {
                    "requestId": "irr_1",
                    "cycleId": "ivc_old",
                    "versionId": "iv_old",
                    "treeHash": H2,
                    "verdict": "approve",
                }
            ]
        )
    )
    assert ev.status == "waiting"
    assert ev.required_reviews_satisfied == 0


def test_check_evidence_mismatch_from_other_version() -> None:
    ev = evaluate_integration_facts(
        _facts(
            check_reports=[
                {
                    "checkId": "python_pytest",
                    "outcome": "passed",
                    "treeHash": H2,
                    "policyHash": H,
                    "environmentHash": H,
                    "versionId": "iv_other",
                }
            ]
        )
    )
    assert ev.status != "passed"
    assert "check_evidence_mismatch" in ev.blocker_codes or "required_check_not_passed" in ev.blocker_codes


def test_reject_verdict_fails() -> None:
    ev = evaluate_integration_facts(
        _facts(
            review_reports=[
                {
                    "requestId": "irr_1",
                    "cycleId": "ivc_1",
                    "versionId": "iv_1",
                    "treeHash": H,
                    "verdict": "reject",
                }
            ]
        )
    )
    assert ev.status == "failed"


def test_selection_stale_blocks() -> None:
    ev = evaluate_integration_facts(_facts(selection_still_valid=False))
    assert ev.status == "blocked"
    assert "selection_stale" in ev.blocker_codes


def test_failed_check_fails_evaluation() -> None:
    ev = evaluate_integration_facts(
        _facts(
            check_reports=[
                {
                    "checkId": "python_pytest",
                    "outcome": "failed",
                    "treeHash": H,
                    "policyHash": H,
                    "environmentHash": H,
                    "versionId": "iv_1",
                }
            ]
        )
    )
    assert ev.status == "failed"
