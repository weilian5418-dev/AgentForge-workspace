"""Bounded, grant-scoped filesystem access — no general shell or absolute open."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath

from agentforge.contracts.grants import TrustPolicy


class ProjectFilesystemError(Exception):
    """Raised when a path escapes the authorized root or violates policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class FilesystemLimits:
    max_file_bytes: int = 52_428_800
    max_total_read_bytes: int = 209_715_200
    max_files: int = 10_000
    max_depth: int = 32
    allow_follow_symlinks: bool = False

    @classmethod
    def from_trust_policy(cls, policy: TrustPolicy) -> FilesystemLimits:
        return cls(
            max_file_bytes=policy.max_file_bytes,
            max_total_read_bytes=policy.max_total_read_bytes,
            max_files=policy.max_files,
            max_depth=policy.max_depth,
            allow_follow_symlinks=policy.allow_follow_symlinks,
        )


def normalize_authorized_root(path: str | Path) -> Path:
    """Resolve and require an existing real directory (lexical + real target)."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ProjectFilesystemError("path_not_absolute", "Authorized root must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectFilesystemError("root_unresolvable", f"Cannot resolve root: {exc}") from exc
    if not resolved.is_dir():
        raise ProjectFilesystemError("root_not_directory", "Authorized root must be a directory")
    # Reject UNC / device paths for v1 unless already a normal drive path.
    text = str(resolved)
    if text.startswith("\\\\") or text.startswith("//"):
        raise ProjectFilesystemError("unc_unsupported", "UNC roots are not supported in v1")
    return resolved


def compute_directory_identity(root: Path) -> str:
    """Stable-ish identity from inode/device (or Windows file index) + normalized path."""
    st = root.stat()
    inode = getattr(st, "st_ino", 0)
    device = getattr(st, "st_dev", 0)
    payload = f"{device}:{inode}:{str(root).casefold()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_special_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return True
    return not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))


class ProjectFilesystem:
    """Limited operations under one authorized root. Business code must not open host paths directly."""

    def __init__(
        self,
        authorized_root: Path,
        *,
        directory_identity: str,
        limits: FilesystemLimits | None = None,
        secret_roots: list[Path] | None = None,
    ) -> None:
        self.root = normalize_authorized_root(authorized_root)
        self.directory_identity = directory_identity
        self.limits = limits or FilesystemLimits()
        self.secret_roots = [p.resolve(strict=False) for p in (secret_roots or [])]
        live = compute_directory_identity(self.root)
        if live != directory_identity:
            raise ProjectFilesystemError(
                "directory_identity_mismatch",
                "Authorized directory identity no longer matches the grant",
            )

    def root_identity(self) -> str:
        return compute_directory_identity(self.root)

    def _assert_not_secret(self, path: Path) -> None:
        for secret in self.secret_roots:
            try:
                path.relative_to(secret)
            except ValueError:
                continue
            raise ProjectFilesystemError(
                "secret_path_denied",
                "Access to platform secret directories is forbidden",
            )

    def resolve_under_root(
        self, relative: str | PurePath, *, for_write_staging: bool = False
    ) -> Path:
        """Compose path under root — never prefix-match strings alone."""
        if for_write_staging:
            raise ProjectFilesystemError(
                "write_denied",
                "ProjectFilesystem does not write into the authorized user root",
            )
        rel = PurePath(str(relative))
        if rel.is_absolute():
            raise ProjectFilesystemError("absolute_denied", "Relative path required")
        parts = rel.parts
        if any(p in {"", ".", ".."} for p in parts) or ".." in str(rel):
            # Allow "." as empty relative → root; reject ".." components.
            if parts == (".",) or parts == ():
                candidate = self.root
            else:
                if ".." in parts:
                    raise ProjectFilesystemError(
                        "traversal_denied", "Path traversal is not allowed"
                    )
                candidate = self.root.joinpath(*parts)
        else:
            candidate = self.root.joinpath(*parts)

        # Lexical containment before resolve.
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProjectFilesystemError("outside_root", "Path escapes authorized root") from exc

        follow = self.limits.allow_follow_symlinks
        try:
            if candidate.exists() or candidate.is_symlink():
                # Re-check after resolve (TOCTOU: caller must re-verify on open).
                resolved = candidate.resolve(strict=False)
            else:
                # Parent must stay inside root.
                resolved = candidate.parent.resolve(strict=False) / candidate.name
                resolved.parent.relative_to(self.root)
                return candidate
        except (OSError, ValueError) as exc:
            raise ProjectFilesystemError("resolve_failed", f"Path resolve failed: {exc}") from exc

        if not follow and candidate.is_symlink():
            # Symlinks do not expand authorization — deny by default in v1.
            raise ProjectFilesystemError("symlink_denied", "Symlinks are not followed in v1")

        try:
            resolved.relative_to(self.root.resolve(strict=True))
        except ValueError as exc:
            raise ProjectFilesystemError(
                "outside_root",
                "Resolved path escapes authorized root (link or junction)",
            ) from exc

        self._assert_not_secret(resolved)
        return resolved

    def reverify_open_target(self, path: Path) -> Path:
        """Re-validate identity after check-then-open (TOCTOU)."""
        if path.is_symlink() and not self.limits.allow_follow_symlinks:
            raise ProjectFilesystemError("symlink_denied", "Symlinks are not followed in v1")
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(self.root.resolve(strict=True))
        except ValueError as exc:
            raise ProjectFilesystemError("outside_root", "Open target escaped root") from exc
        if _is_special_file(resolved):
            raise ProjectFilesystemError("special_file_denied", "Special files are not readable")
        self._assert_not_secret(resolved)
        live = compute_directory_identity(self.root)
        if live != self.directory_identity:
            raise ProjectFilesystemError(
                "directory_identity_mismatch",
                "Directory was replaced after authorization",
            )
        return resolved

    def list_dir(self, relative: str = ".") -> list[dict[str, str | int | bool]]:
        target = self.resolve_under_root(relative)
        target = self.reverify_open_target(target) if target.exists() else target
        if not target.is_dir():
            raise ProjectFilesystemError("not_directory", "Not a directory")
        entries: list[dict[str, str | int | bool]] = []
        with os.scandir(target) as it:
            for entry in it:
                if len(entries) >= self.limits.max_files:
                    raise ProjectFilesystemError(
                        "too_many_files", "Directory listing exceeds limit"
                    )
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_link = entry.is_symlink()
                    size = entry.stat(follow_symlinks=False).st_size if not is_dir else 0
                except OSError:
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "isDir": is_dir,
                        "isSymlink": is_link,
                        "sizeBytes": size,
                    }
                )
        return entries

    def stat_file(self, relative: str) -> dict[str, str | int | bool]:
        path = self.reverify_open_target(self.resolve_under_root(relative))
        st = path.lstat()
        return {
            "path": relative,
            "sizeBytes": st.st_size,
            "isDir": stat.S_ISDIR(st.st_mode),
            "isSymlink": path.is_symlink(),
            "mtimeNs": getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
        }

    def read_file_chunk(self, relative: str, *, offset: int = 0, limit: int = 65_536) -> bytes:
        if limit <= 0 or limit > self.limits.max_file_bytes:
            raise ProjectFilesystemError("read_limit", "Invalid read limit")
        path = self.reverify_open_target(self.resolve_under_root(relative))
        if path.is_dir():
            raise ProjectFilesystemError("is_directory", "Cannot read a directory as a file")
        size = path.stat().st_size
        if size > self.limits.max_file_bytes:
            raise ProjectFilesystemError("file_too_large", "File exceeds max_file_bytes")
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(limit)
        # Re-verify after read.
        self.reverify_open_target(path)
        return data

    def iter_manifest(
        self,
        *,
        relative: str = ".",
        max_files: int | None = None,
    ) -> Iterator[tuple[str, str, int]]:
        """Yield (rel_posix, sha256, size) for regular files under relative."""
        cap = max_files or self.limits.max_files
        total_bytes = 0
        count = 0
        base = self.reverify_open_target(self.resolve_under_root(relative))

        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            depth = Path(dirpath).relative_to(base).parts
            if len(depth) >= self.limits.max_depth:
                dirnames.clear()
                continue
            # Do not follow link dirs.
            dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_symlink() or _is_special_file(path):
                    continue
                verified = self.reverify_open_target(path)
                size = verified.stat().st_size
                if size > self.limits.max_file_bytes:
                    continue
                total_bytes += size
                count += 1
                if count > cap or total_bytes > self.limits.max_total_read_bytes:
                    raise ProjectFilesystemError("scan_limit", "Manifest scan exceeded limits")
                digest = hashlib.sha256()
                with verified.open("rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                rel = verified.relative_to(self.root).as_posix()
                yield rel, digest.hexdigest(), size
