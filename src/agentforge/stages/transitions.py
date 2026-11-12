"""Pure stage status transitions and ready-gate rules (D10.1)."""

from __future__ import annotations

from agentforge.contracts.stages import StageStatus, StageWaitingReason

# Main happy path.
MAIN_PATH: tuple[StageStatus, ...] = (
    "pending",
    "ready",
    "executing",
    "submission_requested",
    "stopping",
    "frozen",
    "reviewing",
    "released",
)

# Explicit legal edges (from → frozenset of to).
LEGAL_TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    "pending": frozenset({"ready", "blocked"}),
    "ready": frozenset({"executing", "blocked"}),
    "executing": frozenset({"submission_requested", "blocked", "rework"}),
    "submission_requested": frozenset({"stopping", "blocked"}),
    "stopping": frozenset({"frozen", "blocked", "executing"}),
    "frozen": frozenset({"reviewing", "blocked"}),
    "reviewing": frozenset({"released", "rework", "blocked"}),
    "rework": frozenset({"executing", "blocked"}),
    "released": frozenset(),
    "blocked": frozenset(
        {
            "pending",
            "ready",
            "executing",
            "submission_requested",
            "stopping",
            "frozen",
            "reviewing",
            "rework",
        }
    ),
}


def can_transition(current: StageStatus, target: StageStatus) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


def assert_transition(current: StageStatus, target: StageStatus) -> None:
    if not can_transition(current, target):
        raise ValueError(f"illegal stage transition {current} → {target}")


def ready_requires(
    *,
    upstream_required_versions_released: bool,
    plan_still_valid: bool,
    stage_eligible_to_start: bool,
) -> bool:
    """ready is not 'previous status string == completed'."""
    return (
        upstream_required_versions_released
        and plan_still_valid
        and stage_eligible_to_start
    )


def waiting_reason_for_block(reason: StageWaitingReason) -> StageWaitingReason:
    if reason == "none":
        raise ValueError("blocked status should carry an explicit waitingReason")
    return reason


def review_cycle_fingerprint_parts(
    *,
    candidate_set_hash: str,
    contract_hash: str,
    gate_policy_version: int,
    environment_fingerprint: str,
) -> tuple[str, str, int, str]:
    """Stable parts used to bind one ReviewCycle — do not reopen on late results."""
    return (
        candidate_set_hash,
        contract_hash,
        gate_policy_version,
        environment_fingerprint,
    )
