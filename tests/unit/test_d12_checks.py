"""D12.10: check registry, outcome honesty, report parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.integration.checks import (
    CheckError,
    CheckExecutionReceipt,
    PythonPytestV1,
    compile_integration_policy,
    evaluate_check_requirements,
)
from agentforge.integration.reports import ReportError, load_json_report, parse_junit_summary
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.queries.logs import LogQueryError, read_log_slice

H = "a" * 64


def _receipt(**kwargs: object) -> CheckExecutionReceipt:
    base = dict(
        exit_code=0,
        timed_out=False,
        termination_confirmed=True,
        stdout_log_id="stdout",
        stderr_log_id="stderr",
        report_path=None,
        tree_hash_before=H,
        tree_hash_after=H,
        argv_definition_hash=H,
    )
    base.update(kwargs)
    return CheckExecutionReceipt(**base)  # type: ignore[arg-type]


def test_exit_zero_alone_not_enough_without_tests() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(
        _receipt(exit_code=0),
        {"collected": 0, "passedCount": 0, "minimumCollectedTests": 1},
    )
    assert out.outcome == "blocked"
    assert out.reason in {"zero_tests", "insufficient_tests"}


def test_all_skipped_blocked() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(
        _receipt(),
        {
            "collected": 3,
            "passedCount": 0,
            "skippedCount": 3,
            "minimumCollectedTests": 1,
            "allowAllSkipped": False,
        },
    )
    assert out.outcome == "blocked"


def test_untrusted_passed_json_rejected() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(_receipt(), {"passed": True})
    assert out.outcome == "failed"
    assert out.reason == "check_evidence_mismatch"


def test_tree_mutation_fails() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(
        _receipt(tree_hash_after="b" * 64),
        {"collected": 1, "passedCount": 1},
    )
    assert out.outcome == "failed"
    assert out.reason == "content_integrity_failed"


def test_timeout_unknown() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(
        _receipt(timed_out=True, termination_confirmed=False, exit_code=None),
        {"collected": 1, "passedCount": 1},
    )
    assert out.outcome == "unknown"


def test_passed_with_counts() -> None:
    adapter = PythonPytestV1()
    out = adapter.parse_result(
        _receipt(),
        {"collected": 2, "passedCount": 2, "failedCount": 0, "minimumCollectedTests": 1},
    )
    assert out.outcome == "passed"


def test_compile_rejects_empty_without_waiver() -> None:
    with pytest.raises(CheckError):
        compile_integration_policy(policy_ref="p1", check_specs=[])


def test_container_required_no_host_fallback() -> None:
    adapter = PythonPytestV1()
    cfg = adapter.validate_config({"testPaths": ["tests"]})
    with pytest.raises(CheckError) as exc:
        adapter.build_execution_request(
            context={
                "workspacePath": "/tmp/ws",
                "outputDir": "/tmp/out",
                "treeHash": H,
                "versionId": "iver_1",
                "requireContainer": True,
                "containerVerified": False,
            },
            config=cfg,
        )
    assert exc.value.code == "check_capability_unverified"


def test_evaluate_empty_required_not_pass() -> None:
    assert evaluate_check_requirements(required=[], reports=[]) == "blocked"


def test_evaluate_missing_required_blocked() -> None:
    assert (
        evaluate_check_requirements(
            required=[{"checkId": "python_pytest", "required": True}],
            reports=[],
        )
        == "blocked"
    )


def test_junit_parse_and_entity_rejected(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0"?>
        <testsuite tests="2" failures="1" errors="0" skipped="0">
          <testcase name="a"/><testcase name="b"><failure/></testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )
    summary = parse_junit_summary(junit)
    assert summary["collected"] == 2
    assert summary["failedCount"] == 1

    bad = tmp_path / "xxe.xml"
    bad.write_text(
        """<?xml version="1.0"?>
        <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <testsuite tests="1">&xxe;</testsuite>
        """,
        encoding="utf-8",
    )
    with pytest.raises(ReportError) as exc:
        parse_junit_summary(bad)
    assert exc.value.code == "report_unsafe"


def test_load_json_report_object_only(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('{"collected":1,"passedCount":1}', encoding="utf-8")
    assert load_json_report(path)["collected"] == 1
    path.write_text("[1,2]", encoding="utf-8")
    with pytest.raises(ReportError):
        load_json_report(path)


def test_log_slice_bounded(tmp_path: Path) -> None:
    log = tmp_path / "abc.log"
    log.write_text("hello world", encoding="utf-8")
    principal = AuthenticatedPrincipal("c", 1, "user:api")
    slice_ = read_log_slice(log_root=tmp_path, log_id="abc", offset=0, limit=5, principal=principal)
    assert slice_["content"] == "hello"
    assert slice_["truncated"] is True
    with pytest.raises(LogQueryError):
        read_log_slice(
            log_root=tmp_path,
            log_id="../abc",
            offset=0,
            limit=5,
            principal=principal,
        )
