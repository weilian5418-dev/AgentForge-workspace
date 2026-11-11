"""Read-only project precheck and baseline capture (BE-04.4)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agentforge.contracts.grants import DirtyPolicy, ProjectBaseline
from agentforge.contracts.projects import ProjectPrecheckSummary
from agentforge.filesystem.access import ProjectFilesystem, ProjectFilesystemError

BUILD_MANIFEST_NAMES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "CMakeLists.txt",
)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            # Never mutate the repo.
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def inspect_precheck(
    fs: ProjectFilesystem,
    *,
    dirty_policy: DirtyPolicy,
    environment_id: str,
) -> tuple[ProjectPrecheckSummary, ProjectBaseline]:
    notes: list[str] = []
    blockers: list[str] = []
    manifests: list[str] = []
    workspace_hints: list[str] = []

    try:
        entries = fs.list_dir(".")
    except ProjectFilesystemError as exc:
        summary = ProjectPrecheckSummary(
            accessible=False,
            is_git=False,
            dirty=False,
            dirty_policy=dirty_policy,
            blockers=[exc.message],
            status="blocked",
        )
        baseline = ProjectBaseline(
            baseline_id="pending",
            project_id="pending",
            environment_id=environment_id,
            dirty_policy=dirty_policy,
            status="blocked",
            is_git=False,
            scope_strategy="none",
            checked_at=datetime.now(UTC),
            notes=[exc.message],
        )
        return summary, baseline

    for entry in entries:
        name = str(entry["name"])
        if name in BUILD_MANIFEST_NAMES:
            manifests.append(name)
        # Facts only — never execute install scripts from manifests.

    git_dir = fs.root / ".git"
    is_git = git_dir.exists()
    git_commit: str | None = None
    dirty = False
    dirty_summary: dict[str, int] = {}
    status: str = "ready"
    scope = "authorized_root"
    manifest_hash: str | None = None

    if is_git:
        head = _run_git(fs.root, "rev-parse", "HEAD")
        if head is None or head.returncode != 0:
            notes.append("git repository has no usable HEAD commit")
            if dirty_policy == "committed_only":
                blockers.append("no_commit_baseline")
                status = "blocked"
            else:
                status = "partial"
        else:
            git_commit = head.stdout.strip() or None

        porcelain = _run_git(fs.root, "status", "--porcelain")
        if porcelain is not None and porcelain.returncode == 0:
            lines = [ln for ln in porcelain.stdout.splitlines() if ln.strip()]
            staged = unstaged = untracked = 0
            for line in lines:
                code = line[:2]
                if code == "??":
                    untracked += 1
                else:
                    if code[0] != " " and code[0] != "?":
                        staged += 1
                    if code[1] != " " and code[1] != "?":
                        unstaged += 1
            dirty_summary = {
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
            }
            dirty = bool(lines)
            if dirty:
                notes.append(
                    "working tree has modifications; baseline records selection — "
                    "no stash/commit/reset/clean performed"
                )
                if dirty_policy == "committed_only":
                    notes.append("execution baseline uses committed revision only")
                    # Do not auto-read all untracked files.
                elif dirty_policy == "user_must_clean":
                    blockers.append("user_must_clean_working_tree")
                    status = "blocked"
                elif dirty_policy == "controlled_dirty_snapshot":
                    notes.append("controlled dirty snapshot requested; limited file set only")
                    status = "partial"
        workspace_hints.append("later independent workspace needs git worktree (BE-10)")
    else:
        # Non-git: bounded file manifest.
        try:
            rows = list(fs.iter_manifest(relative=".", max_files=2000))
            payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
            manifest_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            scope = "bounded_file_manifest"
            notes.append(f"non-git manifest entries={len(rows)}")
            # Stability: re-hash once; if mismatch → unstable.
            rows2 = list(fs.iter_manifest(relative=".", max_files=2000))
            payload2 = json.dumps(rows2, separators=(",", ":"), ensure_ascii=False)
            if payload2 != payload:
                status = "unstable"
                blockers.append("files_changed_during_scan")
                notes.append("scan observed concurrent changes; not a fixed baseline")
            else:
                status = "ready"
        except ProjectFilesystemError as exc:
            if exc.code == "scan_limit":
                status = "partial"
                notes.append("partial scan due to size limits")
            else:
                status = "blocked"
                blockers.append(exc.message)

    if status == "blocked" and not blockers:
        blockers.append("precheck_blocked")

    summary = ProjectPrecheckSummary(
        accessible=True,
        is_git=is_git,
        git_commit=git_commit,
        dirty=dirty,
        dirty_policy=dirty_policy,
        build_manifests=manifests[:32],
        blockers=blockers[:32],
        workspace_hints=workspace_hints[:32],
        status=status,  # type: ignore[arg-type]
    )
    baseline = ProjectBaseline(
        baseline_id="pending",
        project_id="pending",
        environment_id=environment_id,
        dirty_policy=dirty_policy,
        status=status,  # type: ignore[arg-type]
        is_git=is_git,
        git_commit=git_commit,
        git_dirty_summary=dirty_summary,
        manifest_hash=manifest_hash,
        scope_strategy=scope,
        checked_at=datetime.now(UTC),
        notes=notes[:32],
    )
    return summary, baseline
