"""Bounded streaming project input upload (BE-04.5)."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentforge.contracts.grants import TrustPolicy
from agentforge.contracts.projects import ProjectInputResponse
from agentforge.storage.models import ProjectInputRow

_SAFE_DISPLAY_NAME = re.compile(r"^[^\\/:\0<>\"|?*]+$")


class InputError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _display_name_ok(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    if name in {".", ".."} or name.endswith(".") or name.endswith(" "):
        return False
    return bool(_SAFE_DISPLAY_NAME.match(name))


def next_input_version(session: Session, project_id: str, original_name: str) -> int:
    current = session.scalar(
        select(func.max(ProjectInputRow.input_version)).where(
            ProjectInputRow.project_id == project_id,
            ProjectInputRow.original_name == original_name,
            ProjectInputRow.status == "available",
        )
    )
    return int(current or 0) + 1


def register_streamed_input(
    session: Session,
    *,
    project_id: str,
    file_name: str,
    chunks: Iterator[bytes],
    staging_root: Path,
    trust_policy: TrustPolicy,
    content_type: str | None = None,
    client_sha256: str | None = None,
    max_project_bytes: int = 209_715_200,
) -> ProjectInputResponse:
    """Write stream → hash → land → DB register. Failures leave no `available` input."""
    if not _display_name_ok(file_name):
        raise InputError("invalid_file_name", "Original file name is not allowed")

    existing_total = session.scalar(
        select(func.coalesce(func.sum(ProjectInputRow.size_bytes), 0)).where(
            ProjectInputRow.project_id == project_id,
            ProjectInputRow.status == "available",
        )
    )
    used = int(existing_total or 0)

    staging_root.mkdir(parents=True, exist_ok=True)
    input_id = f"input_{uuid.uuid4().hex[:20]}"
    storage_name = f"{input_id}.bin"
    tmp_path = staging_root / f".{storage_name}.partial"
    final_path = staging_root / storage_name

    digest = hashlib.sha256()
    size = 0
    try:
        with tmp_path.open("wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > trust_policy.max_file_bytes:
                    raise InputError("file_too_large", "Upload exceeds max_file_bytes")
                if used + size > max_project_bytes:
                    raise InputError("project_quota", "Project input quota exceeded")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0 and not trust_policy.allow_zero_byte_inputs:
            raise InputError("empty_file_denied", "Zero-byte inputs are not allowed by policy")
        sha = digest.hexdigest()
        if client_sha256 and client_sha256 != sha:
            raise InputError("hash_mismatch", "Client hash does not match server digest")

        twin = session.scalar(
            select(ProjectInputRow).where(
                ProjectInputRow.project_id == project_id,
                ProjectInputRow.original_name == file_name,
                ProjectInputRow.sha256 == sha,
                ProjectInputRow.status == "available",
            )
        )
        if twin is not None:
            raise InputError("duplicate_content", "Identical input already registered")

        version = next_input_version(session, project_id, file_name)
        tmp_path.replace(final_path)

        row = ProjectInputRow(
            id=input_id,
            project_id=project_id,
            input_version=version,
            original_name=file_name,
            size_bytes=size,
            sha256=sha,
            storage_ref=str(final_path),
            content_type=content_type,
            status="available",
        )
        try:
            session.add(row)
            session.flush()
        except Exception:
            # Incomplete uploads must not appear as available; clean landed file.
            final_path.unlink(missing_ok=True)
            raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return ProjectInputResponse(
        id=row.id,
        project_id=project_id,
        input_version=row.input_version,
        file_name=row.original_name,
        content_type=row.content_type,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        status=row.status,
        storage_ref=row.storage_ref,
        created_at=row.created_at if row.created_at else datetime.now(UTC),
    )
