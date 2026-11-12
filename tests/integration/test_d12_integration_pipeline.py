"""D12.14: end-to-end selection → evaluate → hash evidence (stub/platform layer).

Dual external Agent product acceptance is recorded as a separate evidence gate —
mocks here prove platform mechanism only (R15 §19.1).
"""

from __future__ import annotations

import os

import pytest

from agentforge.contracts.delivery import DeliveryManifestPayload
from agentforge.delivery.manifest import compute_manifest_hash
from agentforge.integration.checks import evaluate_check_requirements
from agentforge.integration.reviews import IntegrationFacts, evaluate_integration_facts

pytestmark = pytest.mark.integration

H = "a" * 64


def test_platform_pipeline_failed_iv_blocks_accept_semantics() -> None:
    """Minimal longitudinal: failed checks → evaluation failed → accept must not pass."""
    check_status = evaluate_check_requirements(
        required=[{"checkId": "python_pytest", "required": True}],
        reports=[{"checkId": "python_pytest", "outcome": "failed"}],
    )
    assert check_status == "failed"
    facts = IntegrationFacts(
        cycle_id="ivc_iv1",
        version_id="iv_1",
        run_id="run_pipe",
        tree_hash=H,
        selection_hash=H,
        contract_hash=H,
        policy_hash=H,
        environment_fingerprint=H,
        selection_still_valid=True,
        build_conflict_free=True,
        content_integrity_ok=True,
        required_checks=[{"checkId": "python_pytest", "required": True, "configHash": H}],
        check_reports=[
            {
                "checkId": "python_pytest",
                "outcome": "failed",
                "treeHash": H,
                "policyHash": H,
                "environmentHash": H,
                "versionId": "iv_1",
            }
        ],
        required_review_roles=[],
        review_requests=[],
        review_reports=[],
    )
    ev = evaluate_integration_facts(facts)
    assert ev.status == "failed"
    # Manifest for failed version stays non-passed
    payload = DeliveryManifestPayload.model_validate(
        {
            "schemaVersion": "1",
            "runId": "run_pipe",
            "integrationVersion": "iv_1",
            "validationCycleId": "ivc_iv1",
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
                    "memberTechnicalStatus": "failed",
                }
            ],
            "checks": [
                {
                    "checkId": "python_pytest",
                    "checkVersion": "v1",
                    "configHash": H,
                    "outcome": "failed",
                }
            ],
            "reviews": [],
            "knownRisks": [
                {"kind": "known_failure", "detail": "pytest_failed", "memberIds": []}
            ],
            "technicalStatus": "failed",
        }
    )
    assert payload.technical_status == "failed"
    assert compute_manifest_hash(payload)


def test_dual_product_external_acceptance_gate_recorded() -> None:
    """R15: two verified external Agent products required for full product acceptance.

    This test records the gate; it does not substitute mocks for real products.
    When AGENTFORGE_DUAL_PRODUCT_EVIDENCE=1 and evidence files exist, assert pass;
    otherwise mark as xfail/skip with explicit reason.
    """
    flag = os.environ.get("AGENTFORGE_DUAL_PRODUCT_EVIDENCE")
    if flag != "1":
        pytest.skip(
            "dual external product evidence not provided "
            "(set AGENTFORGE_DUAL_PRODUCT_EVIDENCE=1 after real A/B product run)"
        )
    evidence = os.environ.get("AGENTFORGE_DUAL_PRODUCT_EVIDENCE_PATH")
    assert evidence and os.path.isfile(evidence), "evidence file missing"
