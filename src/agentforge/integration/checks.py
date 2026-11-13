"""Integration check registry and policy compilation (D12.10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agentforge.driver.binding import sha256_hex

SUPPORTED_CAPABILITIES_HOST = frozenset({"host_process", "filesystem_read", "python_interpreter"})


class CheckError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CheckConfig:
    check_id: str
    version: str
    config_hash: str
    raw: dict[str, Any]
    max_duration_seconds: int
    minimum_collected_tests: int
    allow_all_skipped: bool
    test_paths: tuple[str, ...]
    markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovedCheckRequest:
    check_id: str
    version: str
    config_hash: str
    argv: tuple[str, ...]
    cwd: str
    env_whitelist: dict[str, str]
    max_duration_seconds: int
    argv_definition_hash: str
    tree_hash: str
    version_id: str
    output_dir: str
    execution_mode: str  # host_authorized | container (container may be blocked)


@dataclass(frozen=True, slots=True)
class CheckExecutionReceipt:
    exit_code: int | None
    timed_out: bool
    termination_confirmed: bool
    stdout_log_id: str | None
    stderr_log_id: str | None
    report_path: str | None
    tree_hash_before: str
    tree_hash_after: str
    argv_definition_hash: str


@dataclass(frozen=True, slots=True)
class CheckOutcomeResult:
    outcome: str  # CheckOutcome literal
    structured: dict[str, Any]
    report_hash: str
    reason: str | None = None


class RegisteredIntegrationCheck(Protocol):
    check_id: str
    version: str

    def validate_config(self, config: dict[str, Any]) -> CheckConfig: ...

    def required_capabilities(self, config: CheckConfig) -> frozenset[str]: ...

    def build_execution_request(
        self,
        *,
        context: dict[str, Any],
        config: CheckConfig,
    ) -> ApprovedCheckRequest: ...

    def parse_result(
        self,
        execution: CheckExecutionReceipt,
        structured_report: dict[str, Any] | None,
    ) -> CheckOutcomeResult: ...


class PythonPytestV1:
    check_id = "python_pytest"
    version = "v1"

    def validate_config(self, config: dict[str, Any]) -> CheckConfig:
        if not isinstance(config, dict):
            raise CheckError("invalid_config", "config must be an object")
        forbidden = {"shell", "cmd", "command", "pipInstall", "network"}
        for key in config:
            if key in forbidden:
                raise CheckError("invalid_config", f"forbidden config key {key}")
        test_paths = tuple(str(p) for p in (config.get("testPaths") or ["tests"]))
        for p in test_paths:
            if ".." in p.replace("\\", "/").split("/") or p.startswith("/"):
                raise CheckError("invalid_config", f"illegal test path {p}")
        markers = tuple(str(m) for m in (config.get("markers") or []))
        max_duration = int(config.get("maxDurationSeconds") or 300)
        if max_duration < 1 or max_duration > 3600:
            raise CheckError("invalid_config", "maxDurationSeconds out of range")
        minimum = int(config.get("minimumCollectedTests") or 1)
        if minimum < 0 or minimum > 100_000:
            raise CheckError("invalid_config", "minimumCollectedTests out of range")
        allow_skip = bool(config.get("allowAllSkipped") or False)
        raw = {
            "testPaths": list(test_paths),
            "markers": list(markers),
            "maxDurationSeconds": max_duration,
            "minimumCollectedTests": minimum,
            "allowAllSkipped": allow_skip,
        }
        return CheckConfig(
            check_id=self.check_id,
            version=self.version,
            config_hash=sha256_hex(raw),
            raw=raw,
            max_duration_seconds=max_duration,
            minimum_collected_tests=minimum,
            allow_all_skipped=allow_skip,
            test_paths=test_paths,
            markers=markers,
        )

    def required_capabilities(self, config: CheckConfig) -> frozenset[str]:
        _ = config
        return frozenset({"host_process", "filesystem_read", "python_interpreter"})

    def build_execution_request(
        self,
        *,
        context: dict[str, Any],
        config: CheckConfig,
    ) -> ApprovedCheckRequest:
        python = str(context.get("pythonExecutable") or "python")
        cwd = str(context["workspacePath"])
        output_dir = str(context["outputDir"])
        report_file = f"{output_dir}/pytest_report.json"
        argv = [
            python,
            "-m",
            "pytest",
            *config.test_paths,
            "--override-ini=cache_dir=" + f"{output_dir}/.pytest_cache",
            f"--junitxml={output_dir}/junit.xml",
            "-q",
        ]
        for marker in config.markers:
            argv.extend(["-m", marker])
        # Prefer JSON summary file written by wrapper — not trusted alone.
        argv_definition_hash = sha256_hex({"argv": argv, "cwd": cwd, "report": report_file})
        container_ok = bool(context.get("containerVerified"))
        mode = "container" if container_ok else "host_authorized"
        if context.get("requireContainer") and not container_ok:
            raise CheckError(
                "check_capability_unverified",
                "container execution required but not verified; no host fallback",
            )
        return ApprovedCheckRequest(
            check_id=self.check_id,
            version=self.version,
            config_hash=config.config_hash,
            argv=tuple(argv),
            cwd=cwd,
            env_whitelist={
                **{
                    k: v
                    for k, v in __import__("os").environ.items()
                    if k in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
                },
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_ADDOPTS": "",
            },
            max_duration_seconds=config.max_duration_seconds,
            argv_definition_hash=argv_definition_hash,
            tree_hash=str(context["treeHash"]),
            version_id=str(context["versionId"]),
            output_dir=output_dir,
            execution_mode=mode,
        )

    def parse_result(
        self,
        execution: CheckExecutionReceipt,
        structured_report: dict[str, Any] | None,
    ) -> CheckOutcomeResult:
        if execution.argv_definition_hash != (structured_report or {}).get(
            "argvDefinitionHash", execution.argv_definition_hash
        ):
            # Prefer receipt; mismatch with agent-supplied report → invalid
            if structured_report and structured_report.get("argvDefinitionHash") not in {
                None,
                execution.argv_definition_hash,
            }:
                return CheckOutcomeResult(
                    outcome="failed",
                    structured={"reason": "argv_mismatch"},
                    report_hash=sha256_hex({"fail": "argv_mismatch"}),
                    reason="check_evidence_mismatch",
                )
        if execution.tree_hash_before != execution.tree_hash_after:
            return CheckOutcomeResult(
                outcome="failed",
                structured={"reason": "tree_mutated"},
                report_hash=sha256_hex({"fail": "tree_mutated"}),
                reason="content_integrity_failed",
            )
        if execution.timed_out or not execution.termination_confirmed:
            return CheckOutcomeResult(
                outcome="unknown",
                structured={
                    "timedOut": execution.timed_out,
                    "terminationConfirmed": execution.termination_confirmed,
                },
                report_hash=sha256_hex({"unknown": "timeout_or_unconfirmed"}),
                reason="needs_reconciliation",
            )
        if execution.exit_code is None:
            return CheckOutcomeResult(
                outcome="unknown",
                structured={},
                report_hash=sha256_hex({"unknown": "no_exit"}),
                reason="unknown_exit",
            )

        report = dict(structured_report or {})
        # Do not trust a bare passed.json without counts + exit alignment
        if report.get("passed") is True and not any(
            k in report for k in ("collected", "passedCount", "summary")
        ):
            return CheckOutcomeResult(
                outcome="failed",
                structured={"reason": "untrusted_passed_flag"},
                report_hash=sha256_hex({"fail": "untrusted_passed"}),
                reason="check_evidence_mismatch",
            )

        collected = int(report.get("collected") or report.get("summary", {}).get("collected") or 0)
        passed = int(report.get("passedCount") or report.get("summary", {}).get("passed") or 0)
        failed = int(report.get("failedCount") or report.get("summary", {}).get("failed") or 0)
        errors = int(report.get("errorCount") or report.get("summary", {}).get("errors") or 0)
        skipped = int(report.get("skippedCount") or report.get("summary", {}).get("skipped") or 0)
        minimum = int(report.get("minimumCollectedTests") or 1)
        allow_all_skipped = bool(report.get("allowAllSkipped") or False)

        structured = {
            "collected": collected,
            "passedCount": passed,
            "failedCount": failed,
            "errorCount": errors,
            "skippedCount": skipped,
            "exitCode": execution.exit_code,
            "timedOut": execution.timed_out,
            "terminationConfirmed": execution.termination_confirmed,
            "treeHash": execution.tree_hash_after,
        }
        digest = sha256_hex(structured)

        if collected < minimum:
            return CheckOutcomeResult(
                outcome="blocked",
                structured=structured,
                report_hash=digest,
                reason="insufficient_tests",
            )
        if collected > 0 and passed == 0 and skipped == collected and not allow_all_skipped:
            return CheckOutcomeResult(
                outcome="blocked",
                structured=structured,
                report_hash=digest,
                reason="all_skipped",
            )
        if failed or errors or execution.exit_code != 0:
            return CheckOutcomeResult(
                outcome="failed",
                structured=structured,
                report_hash=digest,
                reason="tests_failed",
            )
        if passed == 0 and collected == 0:
            return CheckOutcomeResult(
                outcome="blocked",
                structured=structured,
                report_hash=digest,
                reason="zero_tests",
            )
        return CheckOutcomeResult(outcome="passed", structured=structured, report_hash=digest)


class CheckRegistry:
    def __init__(self) -> None:
        self._checks: dict[tuple[str, str], RegisteredIntegrationCheck] = {}

    def register(self, check: RegisteredIntegrationCheck) -> None:
        self._checks[(check.check_id, check.version)] = check

    def get(self, check_id: str, version: str) -> RegisteredIntegrationCheck:
        key = (check_id, version)
        if key not in self._checks:
            raise CheckError("check_not_registered", f"{check_id}@{version}")
        return self._checks[key]

    def list_ids(self) -> list[tuple[str, str]]:
        return sorted(self._checks)


def default_registry() -> CheckRegistry:
    registry = CheckRegistry()
    registry.register(PythonPytestV1())
    return registry


@dataclass(frozen=True, slots=True)
class CompiledIntegrationPolicy:
    policy_ref: str
    policy_hash: str
    required_checks: tuple[CheckConfig, ...]
    required_review_roles: tuple[str, ...]
    environment_fingerprint: str


def compile_integration_policy(
    *,
    policy_ref: str,
    check_specs: list[dict[str, Any]],
    required_review_roles: list[str] | None = None,
    environment_capabilities: frozenset[str] | None = None,
    registry: CheckRegistry | None = None,
    non_software_waiver: str | None = None,
) -> CompiledIntegrationPolicy:
    reg = registry or default_registry()
    caps = (
        environment_capabilities
        if environment_capabilities is not None
        else SUPPORTED_CAPABILITIES_HOST
    )
    if not check_specs:
        if not non_software_waiver:
            raise CheckError(
                "invalid_config",
                "required checks empty without explicit non-software waiver",
            )
    compiled: list[CheckConfig] = []
    for spec in check_specs:
        check_id = str(spec["checkId"])
        version = str(spec.get("checkVersion") or "v1")
        adapter = reg.get(check_id, version)
        cfg = adapter.validate_config(dict(spec.get("config") or {}))
        needed = adapter.required_capabilities(cfg)
        if not needed <= caps:
            raise CheckError(
                "check_capability_unverified", f"missing capabilities {sorted(needed - caps)}"
            )
        compiled.append(cfg)
    body = {
        "policyRef": policy_ref,
        "checks": [
            {
                "checkId": c.check_id,
                "checkVersion": c.version,
                "configHash": c.config_hash,
            }
            for c in compiled
        ],
        "requiredReviewRoles": list(required_review_roles or []),
        "waiver": non_software_waiver,
    }
    return CompiledIntegrationPolicy(
        policy_ref=policy_ref,
        policy_hash=sha256_hex(body),
        required_checks=tuple(compiled),
        required_review_roles=tuple(required_review_roles or []),
        environment_fingerprint=sha256_hex({"capabilities": sorted(caps)}),
    )


def evaluate_check_requirements(
    *,
    required: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> str:
    """Aggregate required check outcomes — empty required does not all([]) pass."""
    if not required:
        return "blocked"
    by_id = {str(r.get("checkId")): r for r in reports}
    for item in required:
        cid = str(item.get("checkId"))
        if not item.get("required", True):
            continue
        rep = by_id.get(cid)
        if rep is None:
            return "blocked"
        outcome = rep.get("outcome")
        if outcome == "passed":
            continue
        if outcome in {"failed", "timeout"}:
            return "failed"
        if outcome in {"unknown", "not_run", "blocked"}:
            return "blocked"
        return "failed"
    return "passed"
