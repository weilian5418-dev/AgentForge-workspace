"""Normalize wide adapter probe dicts into recovery contracts (D11.9)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from agentforge.contracts.recovery import (
    HandoffRequiredResult,
    NativeObservation,
    ObservationSupport,
    ResumeCoordinatorResult,
    ResumeExistingResult,
    ResumeFailedResult,
    ResumeUnknownResult,
    ResumeUnsupportedResult,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _support(raw: dict[str, Any]) -> ObservationSupport:
    value = raw.get("support") or raw.get("status") or "unknown"
    if value in {"supported", "unsupported", "partial", "unknown"}:
        return cast(ObservationSupport, value)
    if value in {"ok", "confirmed", "succeeded"}:
        return "supported"
    if value in {"failed"}:
        return "unknown"
    return "unknown"


def normalize_native_observation(
    raw: dict[str, Any],
    *,
    instance_id: str,
    attempt_id: str | None = None,
    generation: int | None = None,
    workspace_id: str | None = None,
    native_session_id: str | None = None,
    expected_session_ref: str | None = None,
) -> NativeObservation:
    """Missing safety facts become unknown — never invent False defaults."""
    support = _support(raw)
    running = raw.get("running")
    children = raw.get("childrenRunning", raw.get("children_running"))
    writes = raw.get("writesQuiesced", raw.get("writes_quiesced"))
    session_ref = raw.get("sessionRef") or raw.get("session_ref")
    observed_ws = raw.get("workspaceId") or raw.get("workspace_id")

    # Echo-only payloads without real probe markers are unknown.
    if support == "supported" and raw.get("echoOnly") is True:
        support = "unknown"
    if expected_session_ref and session_ref and session_ref != expected_session_ref:
        support = "unknown"
    if workspace_id and observed_ws and observed_ws != workspace_id:
        support = "unknown"

    # If adapter omitted safety fields, force unknown even if it claimed supported.
    if any(v is None for v in (running, children, writes)):
        if support == "supported":
            support = "unknown"

    return NativeObservation.model_validate(
        {
            "schemaVersion": int(raw.get("schemaVersion") or raw.get("schema_version") or 1),
            "sessionRef": session_ref,
            "nativeSessionId": raw.get("nativeSessionId")
            or raw.get("native_session_id")
            or native_session_id,
            "instanceId": instance_id,
            "attemptId": attempt_id or raw.get("attemptId") or raw.get("attempt_id"),
            "generation": generation if generation is not None else raw.get("generation"),
            "workspaceId": observed_ws or workspace_id,
            "actualCwd": raw.get("actualCwd") or raw.get("actual_cwd") or raw.get("cwd"),
            "running": running if isinstance(running, bool) else None,
            "childrenRunning": children if isinstance(children, bool) else None,
            "writesQuiesced": writes if isinstance(writes, bool) else None,
            "observedAt": raw.get("observedAt") or raw.get("observed_at") or _now(),
            "support": support,
            "evidenceRef": raw.get("evidenceRef") or raw.get("evidence_ref"),
        }
    )


def normalize_resume_result(
    raw: dict[str, Any],
    *,
    expected_session_ref: str,
    expected_native_session_id: str,
) -> ResumeCoordinatorResult:
    """Adapter must not quietly mint a new session and call it resumed."""
    kind = raw.get("kind") or raw.get("status") or raw.get("support") or "unknown"
    detail = raw.get("detail") or raw.get("message")

    new_ref = raw.get("sessionRef") or raw.get("session_ref")
    new_native = raw.get("nativeSessionId") or raw.get("native_session_id")

    if kind in {"handoff_required", "handoffRequired"}:
        return HandoffRequiredResult.model_validate(
            {
                "kind": "handoff_required",
                "reasonCodes": raw.get("reasonCodes") or raw.get("reason_codes") or [],
                "detail": detail,
            }
        )
    if kind in {"unsupported"}:
        return ResumeUnsupportedResult(kind="unsupported", detail=detail)
    if kind in {"failed"}:
        return ResumeFailedResult(
            kind="failed",
            error_code=str(raw.get("errorCode") or raw.get("error_code") or "resume_failed"),
            detail=detail,
        )
    if kind in {"unknown"}:
        return ResumeUnknownResult(
            kind="unknown",
            detail=detail,
            possibly_executed=bool(raw.get("possiblyExecuted", raw.get("possibly_executed", True))),
        )

    if kind in {"resumed_existing", "resumed", "succeeded", "supported"}:
        # Same session identity required — otherwise force handoff_required.
        if new_ref and new_ref != expected_session_ref:
            return HandoffRequiredResult(
                kind="handoff_required",
                reason_codes=["resume_unsupported"],
                detail="adapter returned a different sessionRef; treat as handoff",
            )
        if new_native and new_native != expected_native_session_id:
            return HandoffRequiredResult(
                kind="handoff_required",
                reason_codes=["resume_unsupported"],
                detail="adapter returned a different nativeSessionId; treat as handoff",
            )
        evidence = raw.get("evidenceRef") or raw.get("evidence_ref")
        if not evidence:
            return ResumeUnknownResult(
                kind="unknown",
                detail="resume claimed without evidenceRef",
                possibly_executed=True,
            )
        return ResumeExistingResult.model_validate(
            {
                "kind": "resumed_existing",
                "sessionRef": new_ref or expected_session_ref,
                "nativeSessionId": new_native or expected_native_session_id,
                "evidenceRef": evidence,
                "attemptId": raw.get("attemptId") or raw.get("attempt_id"),
            }
        )

    # Silent create markers are never resume.
    if raw.get("createdSession") or raw.get("newSession") or raw.get("created"):
        return HandoffRequiredResult(
            kind="handoff_required",
            reason_codes=["handoff_confirmation_required"],
            detail="adapter created a session during resume; handoff required",
        )

    return ResumeUnknownResult(
        kind="unknown",
        detail=detail or f"unrecognized resume payload kind={kind}",
        possibly_executed=True,
    )
