"""Safe git invocation: argv arrays only, sanitized env, no shell (D07)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitSafeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Deny network by default; fetch must pass allow_network=True after separate auth.
_SAFE_ENV_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TMP",
    "TEMP",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
}


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


def git_executable() -> str:
    exe = shutil.which("git")
    if not exe:
        raise GitSafeError("git_missing", "git executable not found on PATH")
    return exe


def _null_config_path() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def _empty_hooks_path() -> str:
    """Platform empty hooks directory (Windows cannot use /dev/null as hooksPath)."""
    base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "agentforge_empty_hooks"
    base.mkdir(parents=True, exist_ok=True)
    return str(base.resolve())


def _safe_env(*, allow_network: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS and v}
    null = _null_config_path()
    env["GIT_CONFIG_GLOBAL"] = null
    env["GIT_CONFIG_SYSTEM"] = null
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    if not allow_network:
        env["GIT_ASKPASS"] = ""
        env["SSH_ASKPASS"] = ""
    return env


def run_git(
    repo: Path | None,
    args: list[str],
    *,
    allow_network: bool = False,
    timeout: int = 60,
    check: bool = False,
) -> GitResult:
    """Run `git` with an argument list. Never pass a shell string."""
    if allow_network:
        raise GitSafeError(
            "network_not_authorized",
            "git network operations require independent authorization (not enabled in D07 default)",
        )
    exe = git_executable()
    hooks = _empty_hooks_path()
    cmd = [exe]
    # Force no hooks / no rewrite of external commands.
    cmd.extend(
        [
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            "core.useBuiltinFSMonitor=false",
            "-c",
            "diff.external=",
            "-c",
            "merge.tool=",
            "-c",
            "sequence.editor=",
        ]
    )
    if repo is not None:
        cmd.extend(["-C", str(repo)])
    cmd.extend(args)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_safe_env(allow_network=False),
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitSafeError("git_timeout", f"git timed out: {' '.join(args[:3])}") from exc
    except OSError as exc:
        raise GitSafeError("git_exec_failed", str(exc)) from exc
    result = GitResult(completed.returncode, completed.stdout or "", completed.stderr or "")
    if check and result.returncode != 0:
        raise GitSafeError(
            "git_failed",
            f"git {' '.join(args[:4])} failed: {result.stderr.strip() or result.stdout.strip()}",
        )
    return result


def probe_repo_limits(repo: Path) -> list[str]:
    """Return restriction notes; empty repo / submodule / LFS → restricted."""
    notes: list[str] = []
    head = run_git(repo, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0:
        notes.append("no_commits:blocked_until_initial_commit")
    sub = run_git(repo, ["submodule", "status"])
    if sub.returncode == 0 and sub.stdout.strip():
        notes.append("submodule_present:restricted_unverified")
    lfs = run_git(repo, ["lfs", "env"])
    if lfs.returncode == 0 and "git-lfs" in (lfs.stdout + lfs.stderr).lower():
        notes.append("lfs_detected:restricted_unverified")
    return notes
