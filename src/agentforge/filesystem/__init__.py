"""Controlled project filesystem boundary (BE-04.2)."""

from agentforge.filesystem.access import (
    FilesystemLimits,
    ProjectFilesystem,
    ProjectFilesystemError,
    compute_directory_identity,
    normalize_authorized_root,
)

__all__ = [
    "FilesystemLimits",
    "ProjectFilesystem",
    "ProjectFilesystemError",
    "compute_directory_identity",
    "normalize_authorized_root",
]
