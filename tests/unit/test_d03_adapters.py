"""D03 adapter honesty: unsupported/unknown are normal; no fake auto."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agentforge.adapters.cli_agent import CliCodingAgentAdapter
from agentforge.adapters.cursor_agent import CursorAgentAdapter
from agentforge.adapters.registry import get_adapter, list_adapter_ids


def test_registry_lists_two_candidates() -> None:
    ids = list_adapter_ids()
    assert ids == ["cli_coding_agent", "cursor_agent"]
    assert get_adapter("cursor_agent").adapter_id == "cursor_agent"
    assert get_adapter("cli_coding_agent").adapter_id == "cli_coding_agent"


def test_cursor_matrix_is_manual_assisted_in_d03() -> None:
    matrix = CursorAgentAdapter().validate_capabilities("pending")
    assert matrix.capabilities["approvalRoundtrip"] == "unsupported"
    assert matrix.capabilities["createSession"] == "unknown"
    assert matrix.access_level() == "manual_assisted"
    assert matrix.limits.get("nativeApproval") == (
        "platform_ledger_ready_product_roundtrip_unevidenced"
    )


def test_cli_matrix_events_unsupported() -> None:
    matrix = CliCodingAgentAdapter().validate_capabilities("pending")
    assert matrix.capabilities["events"] == "unsupported"
    assert matrix.access_level() == "manual_assisted"
    assert (
        CliCodingAgentAdapter().respond_approval(
            session_ref="s", native_request_id="n", decision="allow"
        )["status"]
        == "unsupported"
    )


def test_create_session_not_faked() -> None:
    for adapter in (CursorAgentAdapter(), CliCodingAgentAdapter()):
        result = adapter.create_session(
            profile_id="aprof_x", workspace_id="ws_x", operation_id="op_x", context={}
        )
        assert result["status"] in {"unknown", "unsupported"}


def test_passive_discover_uses_which_without_launch(tmp_path: Path) -> None:
    fake = tmp_path / "cursor.exe"
    fake.write_text("x", encoding="utf-8")
    with patch("agentforge.adapters.cursor_agent.shutil.which", return_value=str(fake)):
        with patch("agentforge.adapters.cursor_agent.Path.home", return_value=tmp_path):
            found = CursorAgentAdapter().discover("windows_native")
    assert found
    assert all(
        "no_session_started" in row.get("notes", []) or "no_secret_read" in row.get("notes", [])
        for row in found
    )
    assert all(row["authStatus"] == "unknown" for row in found)
