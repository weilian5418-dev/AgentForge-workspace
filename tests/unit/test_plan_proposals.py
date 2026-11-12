"""D08.4: rules/model proposal generation boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentforge.contracts.plans import PlanningContext
from agentforge.planning.context import PlanningContextBuilder, PlanningContextError
from agentforge.planning.proposals import ProposalError, ProposalGenerator, build_rules_draft
from agentforge.storage.models import (
    DirectoryGrantRow,
    Project,
    ProjectBaselineRow,
    ProjectInputRow,
    Run,
)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        key = getattr(row, "id", None)
        if key is not None:
            self.store[(type(row), str(key))] = row

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))

    def scalars(self, stmt: Any) -> Any:
        class _R:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def all(self) -> list[Any]:
                return list(self._rows)

        text = str(stmt).lower()
        if "project_inputs" in text:
            return _R([r for r in self.added if isinstance(r, ProjectInputRow)])
        return _R([])


def _seed_project(session: _MemSession) -> None:
    now = datetime.now(UTC)
    session.add(
        DirectoryGrantRow(
            id="gr_1",
            authorized_user="local",
            environment_id="env_local",
            normalized_root="D:/proj",
            directory_identity="dir_1",
            allowed_operations=["read", "write"],
            grant_version=1,
            status="active",
            created_at=now,
            revoked_at=None,
        )
    )
    session.add(
        Project(
            id="prj_1",
            directory_grant_id="gr_1",
            display_name="demo",
            root_path="D:/proj",
            workspace_root="D:/proj",
            trust_policy={},
            environment_id="env_local",
            grant_version=1,
            current_baseline_id="bl_1",
            grant_status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        ProjectBaselineRow(
            id="bl_1",
            project_id="prj_1",
            environment_id="env_local",
            dirty_policy="committed_only",
            status="ready",
            is_git=True,
            git_commit="abc",
            git_dirty_summary={},
            manifest_hash=None,
            scope_strategy="authorized_root",
            notes=[],
            checked_at=now,
            created_at=now,
        )
    )


def _run(**overrides: Any) -> Run:
    now = datetime.now(UTC)
    base = {
        "id": "run_1",
        "project_id": "prj_1",
        "goal": "ship notes",
        "mode": "auto",
        "status": "planning",
        "status_version": 1,
        "active_generation": 1,
        "last_sequence": 0,
        "execution_profile": "container",
        "failure_policy": "dependency-aware",
        "budget": {},
        "planning_provider_profile_id": None,
        "baseline_id": "bl_1",
        "input_refs": [],
        "outputs": ["markdown"],
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Run(**base)


def test_context_builder_pins_baseline_without_fake_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _MemSession()
    _seed_project(session)
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda _s, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    ctx = PlanningContextBuilder(session).build_for_run(_run(), persist=True)  # type: ignore[arg-type]
    assert ctx.baseline_id == "bl_1"
    assert ctx.context_manifest_id is None
    assert any(r.__class__.__name__ == "PlanningContextRow" for r in session.added)


def test_context_rejects_missing_input(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _MemSession()
    _seed_project(session)
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda _s, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    with pytest.raises(PlanningContextError) as exc:
        PlanningContextBuilder(session).build_for_run(  # type: ignore[arg-type]
            _run(input_refs=["input_missing"]),
            persist=False,
        )
    assert exc.value.code == "input_unavailable"


def test_rules_path_does_not_call_model() -> None:
    called = {"n": 0}

    def boom(**_k: Any) -> Any:
        called["n"] += 1
        raise AssertionError("model must not be invoked on rules path")

    run = _run()
    ctx = PlanningContext(
        context_id="pctx_1",
        project_id="prj_1",
        run_id=run.id,
        baseline_id="bl_1",
        input_ids=[],
        read_scopes=[],
        summary="t",
        content_hash="a" * 64,
        context_manifest_id=None,
        created_at=datetime.now(UTC),
    )
    proposal = ProposalGenerator(model_invoke=boom).generate(
        run=run, context=ctx, source="rules"
    )
    assert called["n"] == 0
    assert proposal.source == "rules"
    assert proposal.model_call_id is None
    assert proposal.draft.goal == "ship notes"
    assert proposal.draft.routing.model_review_count == 0


def test_auto_uses_rules_when_no_provider() -> None:
    run = _run(planning_provider_profile_id=None)
    ctx = PlanningContext(
        context_id="pctx_1",
        project_id="prj_1",
        run_id=run.id,
        baseline_id="bl_1",
        input_ids=[],
        read_scopes=[],
        summary="t",
        content_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    proposal = ProposalGenerator(model_invoke=lambda **_k: None).generate(
        run=run, context=ctx, source="auto"
    )
    assert proposal.source == "rules"


def test_model_source_blocks_without_provider() -> None:
    run = _run(planning_provider_profile_id=None)
    ctx = PlanningContext(
        context_id="pctx_1",
        project_id="prj_1",
        run_id=run.id,
        baseline_id="bl_1",
        input_ids=[],
        read_scopes=[],
        summary="t",
        content_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ProposalError) as exc:
        ProposalGenerator(model_invoke=lambda **_k: build_rules_draft(run=run, context=ctx)).generate(
            run=run, context=ctx, source="model"
        )
    assert exc.value.code == "planning_provider_required"


def test_model_source_requires_real_manifest() -> None:
    run = _run(planning_provider_profile_id="profile_plan")
    ctx = PlanningContext(
        context_id="pctx_1",
        project_id="prj_1",
        run_id=run.id,
        baseline_id="bl_1",
        input_ids=[],
        read_scopes=[],
        summary="t",
        content_hash="a" * 64,
        context_manifest_id=None,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ProposalError) as exc:
        ProposalGenerator(
            model_invoke=lambda **_k: build_rules_draft(run=run, context=ctx)
        ).generate(run=run, context=ctx, source="model")
    assert exc.value.code == "context_manifest_required"
