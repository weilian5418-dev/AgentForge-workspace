"""Principal and resource read authorization for query paths (D12)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from agentforge.storage.models import Project, Run


class QueryAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Safe identity for audit and authz — never holds bearer secret."""

    credential_id: str
    credential_version: int
    actor_ref: str
    scopes: frozenset[str] = frozenset()


def api_principal(*, actor_ref: str = "user:api") -> AuthenticatedPrincipal:
    """Principal after HTTP bearer middleware already authenticated the request."""
    return AuthenticatedPrincipal(
        credential_id="api_bearer",
        credential_version=1,
        actor_ref=actor_ref,
    )


def authorize_run_read(session: Session, principal: AuthenticatedPrincipal, run_id: str) -> Run:
    """Authorize run read within the caller's current session/transaction."""
    if not principal.credential_id or not principal.actor_ref:
        raise QueryAuthError("unauthorized", "principal required")
    run = session.get(Run, run_id)
    if run is None:
        raise QueryAuthError("run_not_found", run_id)
    project = session.get(Project, run.project_id)
    if project is None:
        raise QueryAuthError("project_not_found", run.project_id)
    # Local single-tenant API bearer: any authenticated principal may read.
    # Future multi-tenant scopes can restrict here without changing call sites.
    if "deny_all" in principal.scopes:
        raise QueryAuthError("forbidden", "principal cannot read runs")
    return run
