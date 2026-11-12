"""Stable keys for threads, waits, and scheduler operations."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentforge.contracts.collaboration import GRAPH_VERSION, STATE_SCHEMA_VERSION, GraphBinding


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(payload: dict[str, Any] | str) -> str:
    raw = payload if isinstance(payload, str) else canonical_json(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def binding_hash(binding: GraphBinding) -> str:
    return sha256_hex(
        {
            "runId": binding.run_id,
            "planVersion": binding.plan_version,
            "assignmentVersion": binding.assignment_version,
            "gatePolicyVersion": binding.gate_policy_version,
            "executionGeneration": binding.execution_generation,
            "graphVersion": binding.graph_version,
            "stateSchemaVersion": binding.state_schema_version,
            "checkpointEpoch": binding.checkpoint_epoch,
        }
    )


def build_thread_id(binding: GraphBinding) -> str:
    return f"afg:{binding_hash(binding)}"


def build_operation_key(
    *,
    kind: str,
    run_id: str,
    plan_version: int,
    assignment_version: int,
    generation: int,
    stage_id: str | None = None,
    work_package_id: str | None = None,
    repair_round: int = 0,
) -> str:
    parts = [
        kind,
        run_id,
        f"p{plan_version}",
        f"a{assignment_version}",
        f"g{generation}",
    ]
    if stage_id:
        parts.append(stage_id)
    if work_package_id:
        parts.append(work_package_id)
    if repair_round:
        parts.append(f"r{repair_round}")
    return ":".join(parts)


def build_wait_key(reason: str, scope: dict[str, Any]) -> str:
    return f"{reason}:{sha256_hex(scope)[:16]}"


def default_binding(
    *,
    run_id: str,
    plan_version: int,
    assignment_version: int,
    gate_policy_version: int,
    execution_generation: int,
    checkpoint_epoch: int = 1,
) -> GraphBinding:
    return GraphBinding(
        run_id=run_id,
        plan_version=plan_version,
        assignment_version=assignment_version,
        gate_policy_version=gate_policy_version,
        execution_generation=execution_generation,
        graph_version=GRAPH_VERSION,
        state_schema_version=STATE_SCHEMA_VERSION,
        checkpoint_epoch=checkpoint_epoch,
    )
