"""D08.3: PlanRepository does not own transactions; versions are append-only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentforge.planning.repository import PlanRepository, plan_row_from_spec
from agentforge.storage.models import Plan, Run


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, Any], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        if isinstance(row, Plan):
            self.store[(Plan, (row.run_id, row.version))] = row
        elif isinstance(row, Run):
            self.store[(Run, row.id)] = row
        else:
            key = getattr(row, "id", None)
            if key is not None:
                self.store[(type(row), key)] = row

    def get(self, model: type, key: Any) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        if "from runs" in text or "runs.id" in text:
            for (_model, key), row in self.store.items():
                if isinstance(row, Run):
                    return row
        if "plans" in text:
            plans = [r for r in self.added if isinstance(r, Plan)]
            if not plans:
                return None
            return sorted(plans, key=lambda p: p.version, reverse=True)[0]
        return None

    def scalars(self, stmt: Any) -> Any:
        class _R:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def all(self) -> list[Any]:
                return list(self._rows)

        text = str(stmt).lower()
        if "plans" in text:
            return _R([r for r in self.added if isinstance(r, Plan)])
        return _R([])


def test_plan_versions_are_append_only() -> None:
    session = _MemSession()
    repo = PlanRepository(session)  # type: ignore[arg-type]
    run = Run(
        id="run_1",
        project_id="prj_1",
        goal="ship",
        mode="auto",
        status="planning",
        status_version=1,
        active_generation=1,
        last_sequence=0,
        execution_profile="container",
        failure_policy="dependency-aware",
        budget={},
        planning_provider_profile_id=None,
        input_refs=[],
        outputs=["markdown"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.add_run(run)
    v1 = plan_row_from_spec(
        run_id="run_1",
        version=1,
        plan_hash="a" * 64,
        contract_hash="b" * 64,
        status="proposed",
        summary="v1",
        spec={"goal": "ship"},
        compilation={"status": "valid"},
        context_id="ctx_1",
        baseline_id="bl_1",
        input_ids=["input_1"],
        gate_policy_version=1,
    )
    repo.add_plan(v1)
    v2 = plan_row_from_spec(
        run_id="run_1",
        version=2,
        plan_hash="c" * 64,
        contract_hash="d" * 64,
        status="proposed",
        summary="v2",
        spec={"goal": "ship revised"},
        compilation={"status": "valid"},
        context_id="ctx_1",
        baseline_id="bl_1",
        input_ids=["input_1"],
        gate_policy_version=1,
        supersedes_version=1,
    )
    repo.add_plan(v2)
    assert repo.get_plan("run_1", 1) is v1
    assert repo.get_plan("run_1", 2) is v2
    assert v1.spec_json != v2.spec_json
