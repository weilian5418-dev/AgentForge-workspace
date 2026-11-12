"""D11.12: binding/hash stability — thread ids never invent authority."""

from __future__ import annotations

from agentforge.contracts.collaboration import GRAPH_VERSION, STATE_SCHEMA_VERSION, GraphBinding
from agentforge.driver.binding import (
    binding_hash,
    build_operation_key,
    build_thread_id,
    build_wait_key,
    default_binding,
)


def test_binding_hash_stable_for_same_fields() -> None:
    a = default_binding(
        run_id="run_1",
        plan_version=2,
        assignment_version=1,
        gate_policy_version=1,
        execution_generation=3,
    )
    b = GraphBinding.model_validate(
        {
            "runId": "run_1",
            "planVersion": 2,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
            "executionGeneration": 3,
            "graphVersion": GRAPH_VERSION,
            "stateSchemaVersion": STATE_SCHEMA_VERSION,
            "checkpointEpoch": 1,
        }
    )
    assert binding_hash(a) == binding_hash(b)
    assert build_thread_id(a) == build_thread_id(b)
    assert build_thread_id(a).startswith("afg:")


def test_binding_hash_changes_with_generation() -> None:
    a = default_binding(
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        execution_generation=1,
    )
    b = default_binding(
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        execution_generation=2,
    )
    assert binding_hash(a) != binding_hash(b)


def test_operation_and_wait_keys_are_deterministic() -> None:
    key1 = build_operation_key(
        kind="assign",
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        generation=1,
        stage_id="stage_a",
        work_package_id="wp_a",
    )
    key2 = build_operation_key(
        kind="assign",
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        generation=1,
        stage_id="stage_a",
        work_package_id="wp_a",
    )
    assert key1 == key2
    assert build_wait_key("stop_verification", {"attemptId": "ia_1"}) == build_wait_key(
        "stop_verification", {"attemptId": "ia_1"}
    )
