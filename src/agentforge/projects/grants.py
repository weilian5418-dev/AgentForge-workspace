"""Directory grant issuance and revocation (local CLI / trusted path only)."""

from __future__ import annotations

import getpass
import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.grants import (
    DirectoryGrant,
    DirectoryGrantCreateRequest,
    GrantOperation,
)
from agentforge.filesystem.access import (
    ProjectFilesystemError,
    compute_directory_identity,
    normalize_authorized_root,
)
from agentforge.storage.models import DirectoryGrantRow, ExecutionEnvironmentRow

DEFAULT_GRANT_OPS: list[GrantOperation] = [
    "read_metadata",
    "read_content",
    "baseline_inspect",
]


class GrantError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ensure_native_environment(session: Session) -> ExecutionEnvironmentRow:
    node = platform.node() or "local"
    existing = session.scalar(
        select(ExecutionEnvironmentRow).where(
            ExecutionEnvironmentRow.type == "windows_native",
            ExecutionEnvironmentRow.node_id == node,
        )
    )
    if existing is not None:
        existing.connection_status = "reachable"
        existing.last_checked_at = datetime.now(UTC)
        return existing
    row = ExecutionEnvironmentRow(
        id=f"env_{uuid.uuid4().hex[:16]}",
        type="windows_native",
        node_id=node,
        wsl_distro=None,
        connection_status="reachable",
        last_checked_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def create_directory_grant(
    session: Session,
    request: DirectoryGrantCreateRequest,
    *,
    authorized_user: str | None = None,
) -> DirectoryGrant:
    try:
        root = normalize_authorized_root(request.root_path)
    except ProjectFilesystemError as exc:
        raise GrantError(exc.code, exc.message) from exc

    if request.environment_id:
        env = session.get(ExecutionEnvironmentRow, request.environment_id)
        if env is None:
            raise GrantError("environment_not_found", "Execution environment not found")
    else:
        env = ensure_native_environment(session)

    identity = compute_directory_identity(root)
    # Idempotent: same user + identity + active grant → return existing.
    user = authorized_user or getpass.getuser()
    prior = session.scalar(
        select(DirectoryGrantRow).where(
            DirectoryGrantRow.authorized_user == user,
            DirectoryGrantRow.directory_identity == identity,
            DirectoryGrantRow.status == "active",
        )
    )
    if prior is not None and prior.normalized_root == str(root):
        return _to_contract(prior)

    row = DirectoryGrantRow(
        id=f"grant_{uuid.uuid4().hex[:20]}",
        authorized_user=user,
        environment_id=env.id,
        normalized_root=str(root),
        directory_identity=identity,
        allowed_operations=list(DEFAULT_GRANT_OPS),
        grant_version=1,
        status="active",
    )
    session.add(row)
    session.flush()
    return _to_contract(row)


def revoke_directory_grant(session: Session, grant_id: str) -> DirectoryGrant:
    row = session.get(DirectoryGrantRow, grant_id)
    if row is None:
        raise GrantError("grant_not_found", "Directory grant not found")
    if row.status == "revoked":
        return _to_contract(row)
    row.status = "revoked"
    row.revoked_at = datetime.now(UTC)
    session.flush()
    return _to_contract(row)


def require_active_grant(session: Session, grant_id: str) -> DirectoryGrantRow:
    row = session.get(DirectoryGrantRow, grant_id)
    if row is None:
        raise GrantError("grant_not_found", "Directory grant not found")
    if row.status != "active":
        raise GrantError("grant_revoked", "Directory grant is not active")
    # Live identity check — refuse if directory replaced.
    try:
        live_root = Path(row.normalized_root)
        live_id = compute_directory_identity(normalize_authorized_root(live_root))
    except ProjectFilesystemError as exc:
        raise GrantError(exc.code, exc.message) from exc
    if live_id != row.directory_identity:
        row.status = "superseded"
        session.flush()
        raise GrantError(
            "directory_replaced",
            "Authorized directory was replaced or migrated; grant invalidated",
        )
    return row


def _to_contract(row: DirectoryGrantRow) -> DirectoryGrant:
    ops = [str(x) for x in (row.allowed_operations or [])]
    return DirectoryGrant(
        grant_id=row.id,
        authorized_user=row.authorized_user,
        environment_id=row.environment_id,
        normalized_root=row.normalized_root,
        directory_identity=row.directory_identity,
        allowed_operations=ops,  # type: ignore[arg-type]
        grant_version=row.grant_version,
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )
