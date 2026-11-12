"""D12.4: agent inventory counts and progress honesty."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from agentforge.queries.agents import (
    explain_profile_schedulability,
    summarize_agent_counts,
    summarize_progress,
)


def _profile(**kwargs: object) -> MagicMock:
    p = MagicMock()
    p.id = kwargs.get("id", "prof_1")
    p.installation_id = kwargs.get("installation_id", "inst_1")
    p.adapter_id = kwargs.get("adapter_id", "adapter_1")
    p.adapter_version = kwargs.get("adapter_version", "1.0.0")
    p.config_hash = kwargs.get("config_hash", "a" * 64)
    p.auth_status = kwargs.get("auth_status", "authenticated")
    p.access_level = kwargs.get("access_level", "controlled_auto")
    p.last_validation_id = kwargs.get("last_validation_id", "check_1")
    return p


def _check(**kwargs: object) -> MagicMock:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    c = MagicMock()
    c.profile_id = kwargs.get("profile_id", "prof_1")
    c.adapter_id = kwargs.get("adapter_id", "adapter_1")
    c.adapter_version = kwargs.get("adapter_version", "1.0.0")
    c.environment_id = kwargs.get("environment_id", "local")
    c.product_version = kwargs.get("product_version", "1.0.0")
    c.checked_at = kwargs.get("checked_at", now - timedelta(hours=1))
    c.valid_until = kwargs.get("valid_until", now + timedelta(hours=12))
    c.evidence_refs = kwargs.get("evidence_refs", [f"config:{'a' * 64}"])
    c.results = kwargs.get(
        "results",
        [{"capability": "edit", "support": "supported"}],
    )
    return c


def test_product_count_dedupes_installations() -> None:
    session = MagicMock()
    inst_a = MagicMock(id="i1", product_id="prod_x", environment_id="local")
    inst_b = MagicMock(id="i2", product_id="prod_x", environment_id="local")
    inst_c = MagicMock(id="i3", product_id="prod_y", environment_id="local")

    calls: list[list[object]] = [
        [inst_a, inst_b, inst_c],  # installations
        [],  # profiles
    ]
    idx = {"n": 0}

    def scalars_side(stmt):  # noqa: ANN001, ARG001
        result = MagicMock()
        rows = calls[idx["n"]] if idx["n"] < len(calls) else []
        idx["n"] += 1
        result.all.return_value = rows
        return result

    session.scalars.side_effect = scalars_side
    counts = summarize_agent_counts(session, environment_id="local", as_of=datetime.now(UTC))
    assert counts.discovered_product_count == 2
    assert counts.schedulable_profile_count == 0
    assert counts.inventory_revision >= 1


def test_stale_capability_not_schedulable() -> None:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    profile = _profile()
    installation = MagicMock(
        id="inst_1", environment_id="local", product_version="1.0.0"
    )
    check = _check(checked_at=now - timedelta(hours=48))
    session = MagicMock()
    session.get.side_effect = lambda model, key: {  # noqa: ARG005
        "inst_1": installation,
        "check_1": check,
    }.get(key)

    expl = explain_profile_schedulability(
        session, profile, {"edit"}, as_of=now
    )
    assert expl.category == "not_schedulable"
    assert "capability_stale" in expl.reasons


def test_verified_but_busy_separate_from_schedulable() -> None:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    profile = _profile()
    installation = MagicMock(
        id="inst_1", environment_id="local", product_version="1.0.0"
    )
    check = _check()
    session = MagicMock()
    session.get.side_effect = lambda model, key: {  # noqa: ARG005
        "inst_1": installation,
        "check_1": check,
    }.get(key)

    ok = explain_profile_schedulability(session, profile, {"edit"}, as_of=now)
    assert ok.category == "schedulable"

    busy = explain_profile_schedulability(
        session, profile, {"edit"}, as_of=now, busy_profile_ids={"prof_1"}
    )
    assert busy.category == "verified_but_busy"


def test_idle_active_instance_still_counts() -> None:
    session = MagicMock()
    inst = MagicMock(id="i1", product_id="prod_x", environment_id="local")
    profile = _profile(installation_id="i1")
    instance = MagicMock(
        agent_profile_id="prof_1",
        lifecycle="active",
        activity="idle",
        status="running",
    )

    calls = [[inst], [profile], [instance]]
    idx = {"n": 0}

    def scalars_side(stmt):  # noqa: ANN001, ARG001
        result = MagicMock()
        rows = calls[idx["n"]] if idx["n"] < len(calls) else []
        idx["n"] += 1
        result.all.return_value = rows
        return result

    session.scalars.side_effect = scalars_side
    # Profile not schedulable without check — still count active instance
    session.get.return_value = None
    counts = summarize_agent_counts(
        session, environment_id="local", run_id="run_1", as_of=datetime.now(UTC)
    )
    assert counts.active_instance_count == 1
    assert counts.busy_instance_count == 0


def test_progress_ratio_is_not_run_completion() -> None:
    session = MagicMock()
    run = MagicMock()
    run.id = "run_1"
    run.plan_version = 1

    wp_required = MagicMock(id="wp_1", status="published", required=True)
    wp_optional = MagicMock(id="wp_2", status="ready", required=False)
    release = MagicMock(work_package_ids=["wp_1"], stage_id="stage_1")

    plan = MagicMock()
    plan.spec_json = None  # fall back to WorkPackage.required flags

    delivery = MagicMock(
        acceptance_status="none",
        current_version_id=None,
        technical_status="not_started",
    )

    call_n = {"n": 0}

    def scalars_side(stmt):  # noqa: ANN001, ARG001
        result = MagicMock()
        rows = [[wp_required, wp_optional], [release]][call_n["n"]]
        call_n["n"] += 1
        result.all.return_value = rows
        return result

    session.scalars.side_effect = scalars_side
    session.get.side_effect = lambda model, key: {  # noqa: ARG005
        ("run_1", 1): plan,
        "run_1": delivery,
    }.get(key)

    progress = summarize_progress(session, run)
    assert progress.required_packages_total == 1
    assert progress.required_packages_released == 1
    assert progress.collaboration_progress_ratio == 1.0
    assert progress.acceptance_status == "none"
    # 100% collaboration ≠ completed acceptance / final checks
    assert progress.final_checks_passed == 0
