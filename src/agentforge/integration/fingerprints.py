"""Canonical hashes for integration selection / version identity (D12.8)."""

from __future__ import annotations

from typing import Any

from agentforge.driver.binding import canonical_json, sha256_hex

DEFAULT_MERGE_POLICY_VERSION = "merge-policy-v1"


def selection_fingerprint(payload: dict[str, Any]) -> str:
    """Normalize selection identity fields into a stable SHA-256."""
    return sha256_hex(payload)


def source_bindings_fingerprint(bindings: list[dict[str, Any]]) -> str:
    ordered = sorted(
        bindings,
        key=lambda b: (
            str(b.get("releaseId") or ""),
            str(b.get("candidateId") or ""),
            str(b.get("snapshotId") or ""),
        ),
    )
    return sha256_hex({"bindings": ordered})


def build_selection_hash(
    *,
    run_id: str,
    plan_version: int | None,
    assignment_version: int | None,
    gate_policy_version: int | None,
    active_generation: int,
    handoff_ref: str,
    handoff_hash: str,
    baseline_ref: str | None,
    baseline_tree_hash: str,
    source_bindings: list[dict[str, Any]],
    merge_policy_version: str,
    contract_hash: str,
) -> str:
    return selection_fingerprint(
        {
            "runId": run_id,
            "planVersion": plan_version,
            "assignmentVersion": assignment_version,
            "gatePolicyVersion": gate_policy_version,
            "executionGeneration": active_generation,
            "handoffRef": handoff_ref,
            "handoffHash": handoff_hash,
            "baselineRef": baseline_ref,
            "baselineTreeHash": baseline_tree_hash,
            "sourceBindingsHash": source_bindings_fingerprint(source_bindings),
            "mergePolicyVersion": merge_policy_version,
            "contractHash": contract_hash,
        }
    )


def canonicalize_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """Stable camelCase binding for storage and hashing."""
    return json_loads_dumps(
        {
            "releaseId": binding["releaseId"],
            "candidateId": binding.get("candidateId"),
            "snapshotId": binding["snapshotId"],
            "workPackageId": binding.get("workPackageId"),
            "stageId": binding.get("stageId"),
            "contentHash": binding["contentHash"],
            "treeHash": binding.get("treeHash"),
        }
    )


def json_loads_dumps(payload: dict[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(canonical_json(payload))
