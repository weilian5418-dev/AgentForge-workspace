"""BE-04.6: path binding must not assume correspondence from drive-letter rewrite."""

from __future__ import annotations

from pathlib import Path

from agentforge.projects.bindings import (
    unsupported_binding_result,
    windows_to_wsl_candidate,
)


def test_windows_to_wsl_is_candidate_only(tmp_path: Path) -> None:
    root = tmp_path / "code"
    root.mkdir()
    # On Windows this yields /mnt/<drive>/...; still not proof of reachability.
    candidate = windows_to_wsl_candidate(root)
    if candidate is not None:
        assert candidate.startswith("/mnt/")
        assert "\\" not in candidate


def test_unsupported_combination_is_explicit() -> None:
    result = unsupported_binding_result(
        source_environment_id="env_a",
        target_environment_id="env_b",
        source_path="D:/a",
        target_path="/workspace/a",
    )
    assert result.status == "unsupported"
    assert "unsupported" in result.correspondence_evidence
