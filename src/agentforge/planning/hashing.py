"""Canonical plan / contract hashes — pure, stable, no I/O or timestamps."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentforge.contracts.plans import PlanSpec

PLAN_HASH_ALGO = "plan-hash-v1"
CONTRACT_HASH_ALGO = "contract-hash-v1"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return dict(model.model_dump(mode="json", by_alias=True))
    if isinstance(model, dict):
        return dict(model)
    raise TypeError(f"unsupported hash input: {type(model)!r}")


def compute_contract_hash(deliverables: list[Any]) -> str:
    """Hash the unordered set of delivery contracts (sorted by id)."""
    items = [_dump(d) for d in deliverables]
    items.sort(key=lambda row: str(row.get("id") or ""))
    return _sha256({"algo": CONTRACT_HASH_ALGO, "deliverables": items})


def compute_plan_hash(spec: PlanSpec | dict[str, Any]) -> str:
    """Hash immutable plan definition fields.

    Excludes update timestamps, runtime statuses, random diagnostics, and
    assignment/capability evidence (those bind separately on confirmation).
    """
    raw = _dump(spec)
    # Drop fields that must never affect identity of the plan definition.
    raw.pop("hashAlgoVersion", None)
    payload = {
        "algo": PLAN_HASH_ALGO,
        "baselineId": raw.get("baselineId"),
        "constraints": raw.get("constraints") or [],
        "gatePolicyVersion": raw.get("gatePolicyVersion"),
        "goal": raw.get("goal"),
        "inputIds": sorted(raw.get("inputIds") or []),
        "resourceEstimate": raw.get("resourceEstimate"),
        "roles": sorted(raw.get("roles") or [], key=lambda r: str(r.get("roleId") or "")),
        "routing": raw.get("routing"),
        "stages": sorted(raw.get("stages") or [], key=lambda s: str(s.get("stageId") or "")),
        "workPackages": sorted(raw.get("workPackages") or [], key=lambda w: str(w.get("id") or "")),
    }
    return _sha256(payload)
