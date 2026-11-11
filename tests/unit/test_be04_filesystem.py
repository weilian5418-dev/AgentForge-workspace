"""BE-04.2/04.8: real-filesystem path confinement tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.filesystem.access import (
    ProjectFilesystem,
    ProjectFilesystemError,
    compute_directory_identity,
    normalize_authorized_root,
)


def test_normalize_requires_absolute(tmp_path: Path) -> None:
    with pytest.raises(ProjectFilesystemError) as exc:
        normalize_authorized_root("relative/only")
    assert exc.value.code == "path_not_absolute"


def test_traversal_and_absolute_denied(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "ok.txt").write_text("hi", encoding="utf-8")
    identity = compute_directory_identity(normalize_authorized_root(root))
    fs = ProjectFilesystem(root, directory_identity=identity)

    with pytest.raises(ProjectFilesystemError) as exc:
        fs.resolve_under_root("../outside")
    assert exc.value.code == "traversal_denied"

    with pytest.raises(ProjectFilesystemError):
        fs.resolve_under_root(str(tmp_path / "other"))


def test_read_within_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("内容 with spaces", encoding="utf-8")
    sub = root / "sub dir"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"abc")
    identity = compute_directory_identity(normalize_authorized_root(root))
    fs = ProjectFilesystem(root, directory_identity=identity)
    assert fs.read_file_chunk("a.txt") == "内容 with spaces".encode()
    assert fs.stat_file("sub dir/b.txt")["sizeBytes"] == 3


def test_symlink_outside_denied(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    outside = tmp_path / "secret"
    root.mkdir()
    outside.mkdir()
    (outside / "leak.txt").write_text("nope", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not available")
    identity = compute_directory_identity(normalize_authorized_root(root))
    fs = ProjectFilesystem(root, directory_identity=identity)
    with pytest.raises(ProjectFilesystemError) as exc:
        fs.resolve_under_root("escape/leak.txt")
    assert exc.value.code in {"symlink_denied", "outside_root"}


def test_identity_mismatch_after_replace(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    identity = compute_directory_identity(normalize_authorized_root(root))
    fs = ProjectFilesystem(root, directory_identity=identity)
    # Replace directory (new inode on most FS).
    marker = root / "x.txt"
    marker.write_text("1", encoding="utf-8")
    replaced = tmp_path / "proj_new"
    replaced.mkdir()
    root.rename(tmp_path / "proj_old")
    replaced.rename(root)
    with pytest.raises(ProjectFilesystemError) as exc:
        fs.reverify_open_target(root)
    assert exc.value.code == "directory_identity_mismatch"


def test_chinese_and_space_paths(tmp_path: Path) -> None:
    root = tmp_path / "项目 根"
    root.mkdir()
    target = root / "说明 文档.txt"
    target.write_text("ok", encoding="utf-8")
    identity = compute_directory_identity(normalize_authorized_root(root))
    fs = ProjectFilesystem(root, directory_identity=identity)
    assert fs.read_file_chunk("说明 文档.txt") == b"ok"
