"""Three-phase integration check runs (D12.10)."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.contracts.integrations import (
    CheckPolicyItem,
    ValidationCycleRequest,
    ValidationCycleView,
)
from agentforge.core.config import Settings
from agentforge.driver.binding import sha256_hex
from agentforge.integration.checks import (
    CheckError,
    CheckExecutionReceipt,
    CheckRegistry,
    CompiledIntegrationPolicy,
    compile_integration_policy,
    default_registry,
)
from agentforge.integration.reports import ReportError, load_json_report, parse_junit_summary
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.storage.models import (
    IntegrationCheckReportRow,
    IntegrationCheckRunRow,
    IntegrationValidationCycleRow,
    IntegrationVersionRow,
    Outbox,
    Run,
    RunDeliveryRow,
)
from agentforge.storage.repositories.events import append_domain_fact
from agentforge.storage.uow import db_now, unit_of_work_connection


class CheckWorkerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now(session: Session) -> datetime:
    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)
        return value
    except Exception:
        return datetime.now(UTC)


def open_validation_cycle(
    session: Session,
    *,
    principal: AuthenticatedPrincipal,
    version_id: str,
    body: ValidationCycleRequest,
    policy: CompiledIntegrationPolicy,
) -> ValidationCycleView:
    if not principal.credential_id:
        raise CheckWorkerError("unauthorized", "principal required")
    version = session.get(IntegrationVersionRow, version_id)
    if version is None:
        raise CheckWorkerError("version_not_found", version_id)
    existing = session.scalar(
        select(IntegrationValidationCycleRow).where(
            IntegrationValidationCycleRow.version_id == version_id,
            IntegrationValidationCycleRow.policy_hash == policy.policy_hash,
        )
    )
    # Idempotency via policy+version; request key stored in required_checks meta if needed
    if existing is not None:
        return _cycle_view(existing)

    if not policy.required_checks and not body.policy_ref.endswith(":waiver"):
        raise CheckWorkerError("invalid_config", "empty required checks")

    required = [
        {
            "checkId": c.check_id,
            "checkVersion": c.version,
            "required": True,
            "configHash": c.config_hash,
            "config": c.raw,
        }
        for c in policy.required_checks
    ]
    now = _now(session)
    row = IntegrationValidationCycleRow(
        id=_id("ivc"),
        version_id=version_id,
        policy_hash=policy.policy_hash,
        environment_fingerprint=policy.environment_fingerprint,
        status="open",
        required_checks=required,
        required_review_roles=list(policy.required_review_roles),
        created_at=now,
    )
    session.add(row)
    session.flush()
    return _cycle_view(row)


def _cycle_view(row: IntegrationValidationCycleRow) -> ValidationCycleView:
    items = [
        CheckPolicyItem.model_validate(
            {
                "checkId": i["checkId"],
                "checkVersion": i.get("checkVersion") or i.get("check_version") or "v1",
                "required": bool(i.get("required", True)),
                "configHash": i["configHash"],
            }
        )
        for i in (row.required_checks or [])
    ]
    return ValidationCycleView.model_validate(
        {
            "cycleId": row.id,
            "versionId": row.version_id,
            "policyHash": row.policy_hash,
            "environmentFingerprint": row.environment_fingerprint,
            "status": row.status,
            "requiredChecks": [i.model_dump(mode="json", by_alias=True) for i in items],
            "requiredReviewRoles": list(row.required_review_roles or []),
            "createdAt": row.created_at,
        }
    )


def request_check_run(
    session: Session,
    *,
    cycle_id: str,
    check_id: str,
    check_version: str,
    config_hash: str,
) -> IntegrationCheckRunRow:
    cycle = session.get(IntegrationValidationCycleRow, cycle_id, with_for_update=True)
    if cycle is None:
        raise CheckWorkerError("cycle_not_found", cycle_id)
    existing = session.scalar(
        select(IntegrationCheckRunRow).where(
            IntegrationCheckRunRow.cycle_id == cycle_id,
            IntegrationCheckRunRow.check_id == check_id,
            IntegrationCheckRunRow.check_version == check_version,
            IntegrationCheckRunRow.config_hash == config_hash,
            IntegrationCheckRunRow.attempt_no == 1,
        )
    )
    if existing is not None:
        return existing
    now = _now(session)
    row = IntegrationCheckRunRow(
        id=_id("icr"),
        cycle_id=cycle_id,
        check_id=check_id,
        check_version=check_version,
        config_hash=config_hash,
        attempt_no=1,
        status="not_run",
        operation_ref=None,
        started_at=None,
        finished_at=None,
    )
    session.add(row)
    cycle.status = "checking"
    session.add(
        Outbox(
            id=_id("obx"),
            run_id=None,
            intent_type="integration_check",
            target="integration_check",
            payload={"operationId": row.id, "checkRunId": row.id, "cycleId": cycle_id},
            idempotency_key=f"integration_check:{row.id}",
            delivery_status="pending",
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    return row


ExecutorFn = Callable[[Any], CheckExecutionReceipt]


def process_check_run(
    engine: Engine,
    settings: Settings,
    *,
    check_run_id: str,
    registry: CheckRegistry | None = None,
    executor: ExecutorFn | None = None,
    structured_report: dict[str, Any] | None = None,
) -> IntegrationCheckReportRow:
    """Execute approved check outside claim reuse; persist immutable report."""
    reg = registry or default_registry()
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        run_row = session.get(IntegrationCheckRunRow, check_run_id, with_for_update=True)
        if run_row is None:
            raise CheckWorkerError("check_run_not_found", check_run_id)
        if run_row.status != "not_run":
            # Do not restart possibly still-running processes
            report = session.scalar(
                select(IntegrationCheckReportRow).where(
                    IntegrationCheckReportRow.check_run_id == check_run_id
                )
            )
            if report is not None:
                return report
            raise CheckWorkerError("invalid_state", f"check already {run_row.status}")

        cycle = session.get(IntegrationValidationCycleRow, run_row.cycle_id)
        if cycle is None:
            raise CheckWorkerError("cycle_not_found", run_row.cycle_id)
        version = session.get(IntegrationVersionRow, cycle.version_id)
        if version is None:
            raise CheckWorkerError("version_not_found", cycle.version_id)

        from agentforge.contracts.plans import PlanSpec
        from agentforge.integration.authority import (
            assert_run_deliverable,
            assert_selection_current,
        )
        from agentforge.storage.models import IntegrationSelectionRow

        owner = session.get(Run, version.run_id, with_for_update=True)
        if owner is None:
            raise CheckWorkerError("run_not_found", version.run_id)
        try:
            assert_run_deliverable(session, owner)
            plan = assert_selection_current(
                session, owner, session.get(IntegrationSelectionRow, version.selection_id)
            )
        except ValueError as exc:
            raise CheckWorkerError("selection_stale", str(exc)) from exc
        policy_spec = PlanSpec.model_validate(plan.spec_json).integration_policy
        if policy_spec is None:
            raise CheckWorkerError("policy_not_confirmed", "Missing confirmed integration policy")
        adapter = reg.get(run_row.check_id, run_row.check_version)
        spec = next(
            (
                c
                for c in (cycle.required_checks or [])
                if c.get("checkId") == run_row.check_id
                and c.get("configHash") == run_row.config_hash
            ),
            None,
        )
        if spec is None:
            raise CheckWorkerError("check_not_registered", "check not in cycle policy")
        cfg = adapter.validate_config(dict(spec.get("config") or {}))
        if cfg.config_hash != run_row.config_hash:
            raise CheckWorkerError("check_evidence_mismatch", "config hash drift")

        tree_hash = version.tree_manifest_hash or version.content_hash
        scratch = Path(settings.state_dir) / "checks" / run_row.id
        workspace = Path(settings.state_dir) / "integration_builds" / "_checks" / version.id

        context = {
            "workspacePath": str(workspace),
            "outputDir": str(scratch),
            "treeHash": tree_hash,
            "versionId": version.id,
            "pythonExecutable": __import__("sys").executable,
            "containerVerified": False,
            "requireContainer": True,
        }
        context["requireContainer"] = policy_spec.execution_mode == "container"
        if (
            policy_spec.execution_mode == "host_authorized"
            and owner.execution_profile != "host_authorized"
        ):
            raise CheckWorkerError(
                "check_capability_unverified", "Host execution was not authorized"
            )
        try:
            approved = adapter.build_execution_request(context=context, config=cfg)
        except CheckError as exc:
            raise CheckWorkerError(exc.code, exc.message) from exc

        run_row.status = "unknown"  # in-flight until receipt
        run_row.started_at = _now(session)
        run_row.operation_ref = approved.argv_definition_hash[:32]
        session.flush()
        min_tests = cfg.minimum_collected_tests
        allow_skip = cfg.allow_all_skipped
        approved_req = approved
        tree_hash_fixed = tree_hash
        adapter_ref = adapter
        content_hash_fixed = version.content_hash
        version_id_fixed = version.id
        policy_hash_fixed = cycle.policy_hash

    # Restore exactly this immutable version after the dispatch intent is committed.
    from agentforge.artifacts.store import ArtifactStore
    from agentforge.integration.objects import load_tree, materialize_tree, verify_materialized

    tree = load_tree(
        ArtifactStore(settings.state_dir / "artifact_store"), content_hash_fixed, tree_hash_fixed
    )
    materialize_tree(tree, ArtifactStore(settings.state_dir / "artifact_store"), workspace)
    scratch.mkdir(parents=True, exist_ok=False)
    # Outside short update — execute
    if executor is not None:
        receipt = executor(approved_req)
    else:
        receipt = _host_execute(approved_req, tree_hash=tree_hash_fixed)

    if not verify_materialized(tree, workspace):
        from dataclasses import replace

        receipt = replace(receipt, tree_hash_after="0" * 64)
    report_data = structured_report
    if report_data is None:
        json_path = Path(approved_req.output_dir) / "pytest_report.json"
        junit_path = Path(approved_req.output_dir) / "junit.xml"
        try:
            if json_path.is_file():
                report_data = load_json_report(json_path)
            elif junit_path.is_file():
                report_data = parse_junit_summary(junit_path)
            else:
                report_data = {}
        except ReportError:
            report_data = {}
    report_data = dict(report_data or {})
    report_data["minimumCollectedTests"] = min_tests
    report_data["allowAllSkipped"] = allow_skip
    report_data["argvDefinitionHash"] = approved_req.argv_definition_hash

    outcome = adapter_ref.parse_result(receipt, report_data)

    with unit_of_work_connection(engine) as uow:
        session = uow.session
        run_row = session.get(IntegrationCheckRunRow, check_run_id, with_for_update=True)
        assert run_row is not None
        cycle = session.get(IntegrationValidationCycleRow, run_row.cycle_id)
        assert cycle is not None
        version = session.get(IntegrationVersionRow, cycle.version_id)
        assert version is not None
        if (
            run_row.operation_ref != approved_req.argv_definition_hash[:32]
            or run_row.status != "unknown"
            or version.id != version_id_fixed
            or cycle.policy_hash != policy_hash_fixed
        ):
            raise CheckWorkerError("stale_check_receipt", "Dispatch binding changed")
        now = _now(session)
        run_row.status = outcome.outcome
        run_row.finished_at = now
        report = IntegrationCheckReportRow(
            id=_id("icrep"),
            check_run_id=run_row.id,
            version_id=version.id,
            tree_hash=tree_hash_fixed,
            policy_hash=cycle.policy_hash,
            environment_hash=cycle.environment_fingerprint,
            outcome=outcome.outcome,
            structured_result=outcome.structured,
            evidence_refs=[x for x in [receipt.stdout_log_id, receipt.stderr_log_id] if x],
            report_hash=outcome.report_hash,
            created_at=now,
        )
        session.add(report)
        append_domain_fact(
            session,
            version.run_id,
            "integration.check_completed",
            {
                "checkRunId": run_row.id,
                "cycleId": cycle.id,
                "versionId": version.id,
                "checkId": run_row.check_id,
                "outcome": outcome.outcome,
                "reportHash": outcome.report_hash,
                "treeHash": tree_hash_fixed,
            },
        )
        delivery = session.get(RunDeliveryRow, version.run_id)
        if (
            delivery is not None
            and delivery.current_version_id == version.id
            and outcome.outcome == "failed"
        ):
            delivery.technical_status = "failed"
            delivery.updated_at = now
        session.flush()
        return report


def _host_execute(approved: Any, *, tree_hash: str) -> CheckExecutionReceipt:
    """Explicit host-authorized mode, bounded file capture; never claim an OS sandbox."""
    import threading

    log_dir = Path(approved.output_dir)
    stdout_path, stderr_path = log_dir / "stdout.log", log_dir / "stderr.log"
    if approved.execution_mode != "host_authorized":
        raise CheckWorkerError("check_capability_unverified", "No verified container executor")

    def drain(pipe: Any, path: Path) -> None:
        remaining = 2 * 1024 * 1024
        with path.open("wb") as log:
            while chunk := pipe.read(8192):
                if remaining:
                    written = chunk[:remaining]
                    log.write(written)
                    remaining -= len(written)
        pipe.close()

    from agentforge.integration.processes import ProcessTree

    process_tree = ProcessTree()
    proc = process_tree.start(
        list(approved.argv),
        cwd=approved.cwd,
        env=approved.env_whitelist,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    readers = [
        threading.Thread(target=drain, args=(proc.stdout, stdout_path), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, stderr_path), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        proc.wait(timeout=approved.max_duration_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process_tree.terminate()
        proc.wait(timeout=10)
    for reader in readers:
        reader.join(timeout=2)
    # Host PID exit alone cannot prove descendant quiescence. On an authorized host,
    # terminate any remaining process group before recording a safe receipt.
    terminated = (
        not timed_out and process_tree.quiescent() and not any(r.is_alive() for r in readers)
    )
    if not terminated:
        process_tree.terminate()
        for reader in readers:
            reader.join(timeout=2)
        terminated = process_tree.quiescent() and not any(r.is_alive() for r in readers)
    process_tree.close()
    return CheckExecutionReceipt(
        exit_code=proc.returncode,
        timed_out=timed_out,
        termination_confirmed=terminated,
        stdout_log_id=f"{log_dir.name}/stdout",
        stderr_log_id=f"{log_dir.name}/stderr",
        report_path=str(log_dir / "junit.xml"),
        tree_hash_before=tree_hash,
        tree_hash_after=tree_hash,
        argv_definition_hash=approved.argv_definition_hash,
    )


def start_validation_for_version(
    engine: Engine,
    settings: Settings,
    *,
    principal: AuthenticatedPrincipal,
    version_id: str,
    body: ValidationCycleRequest,
    check_specs: list[dict[str, Any]] | None = None,
) -> ValidationCycleView:
    import sys

    from agentforge.contracts.plans import PlanSpec
    from agentforge.integration.authority import assert_run_deliverable, assert_selection_current
    from agentforge.queries.authorization import authorize_run_read
    from agentforge.storage.models import IntegrationSelectionRow

    with unit_of_work_connection(engine) as uow:
        version = uow.session.get(IntegrationVersionRow, version_id)
        if version is None:
            raise CheckWorkerError("version_not_found", version_id)
        authorized = authorize_run_read(uow.session, principal, version.run_id)
        run = uow.session.get(Run, authorized.id, with_for_update=True, populate_existing=True)
        if run is None:
            raise CheckWorkerError("run_not_found", version.run_id)
        try:
            assert_run_deliverable(uow.session, run)
            plan = assert_selection_current(
                uow.session, run, uow.session.get(IntegrationSelectionRow, version.selection_id)
            )
        except ValueError as exc:
            raise CheckWorkerError("selection_stale", str(exc)) from exc
        confirmed = PlanSpec.model_validate(plan.spec_json).integration_policy
        if confirmed is None or confirmed.policy_ref != body.policy_ref:
            raise CheckWorkerError(
                "policy_not_confirmed", "Policy must be part of the approved plan"
            )
        if check_specs is not None and check_specs != confirmed.checks:
            raise CheckWorkerError("policy_not_confirmed", "Caller cannot replace confirmed checks")
        try:
            policy = compile_integration_policy(
                policy_ref=body.policy_ref,
                check_specs=confirmed.checks,
                required_review_roles=confirmed.required_review_roles,
                environment_capabilities=frozenset(
                    {"host_process", "filesystem_read", "python_interpreter"}
                )
                if confirmed.execution_mode == "host_authorized"
                and run.execution_profile == "host_authorized"
                else frozenset(),
            )
        except CheckError as exc:
            raise CheckWorkerError(exc.code, exc.message) from exc
        from dataclasses import replace

        policy = replace(
            policy,
            environment_fingerprint=sha256_hex(
                {
                    "python": sys.executable,
                    "pythonVersion": sys.version,
                    "mode": confirmed.execution_mode,
                    "policyHash": policy.policy_hash,
                }
            ),
        )
        cycle = open_validation_cycle(
            uow.session,
            principal=principal,
            version_id=version_id,
            body=body,
            policy=policy,
        )
        for cfg in policy.required_checks:
            request_check_run(
                uow.session,
                cycle_id=cycle.cycle_id,
                check_id=cfg.check_id,
                check_version=cfg.version,
                config_hash=cfg.config_hash,
            )
        return cycle
