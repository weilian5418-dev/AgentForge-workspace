"""Persist discovered products/installations/profiles and capability evidence (D03)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.adapters.registry import get_adapter
from agentforge.contracts.agents import (
    AgentInstallation,
    AgentProduct,
    AgentProfile,
    CapabilityCheck,
    CapabilityResult,
)
from agentforge.discovery.service import DiscoveredInstallation
from agentforge.storage.models import (
    AgentInstallationRow,
    AgentProductRow,
    AgentProfileRow,
    CapabilityCheckRow,
)


class DiscoveryRegistryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ensure_product(session: Session, *, product_id: str, name: str | None = None) -> AgentProduct:
    row = session.get(AgentProductRow, product_id)
    if row is None:
        row = AgentProductRow(
            id=product_id,
            name=name or product_id,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()
    return AgentProduct(product_id=row.id, name=row.name)


def register_installation(
    session: Session,
    item: DiscoveredInstallation,
) -> AgentInstallation:
    ensure_product(session, product_id=item.product_id, name=item.product_id)
    existing = session.scalar(
        select(AgentInstallationRow).where(
            AgentInstallationRow.product_id == item.product_id,
            AgentInstallationRow.location_ref == item.location_ref,
            AgentInstallationRow.environment_id == item.environment_id,
        )
    )
    if existing is not None:
        return AgentInstallation(
            installation_id=existing.id,
            product_id=existing.product_id,
            product_version=existing.product_version,
            environment_id=existing.environment_id,
            location_ref=existing.location_ref,
            discovered_at=existing.discovered_at,
        )
    row = AgentInstallationRow(
        id=f"inst_{uuid.uuid4().hex[:16]}",
        product_id=item.product_id,
        product_version=item.product_version,
        environment_id=item.environment_id,
        location_ref=item.location_ref,
        discovered_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return AgentInstallation(
        installation_id=row.id,
        product_id=row.product_id,
        product_version=row.product_version,
        environment_id=row.environment_id,
        location_ref=row.location_ref,
        discovered_at=row.discovered_at,
    )


def create_profile(
    session: Session,
    *,
    installation_id: str,
    adapter_id: str,
) -> tuple[AgentProfile, CapabilityCheck]:
    installation = session.get(AgentInstallationRow, installation_id)
    if installation is None:
        raise DiscoveryRegistryError("installation_not_found", "Installation not found")
    try:
        adapter = get_adapter(adapter_id)
    except KeyError as exc:
        raise DiscoveryRegistryError("adapter_not_found", str(exc)) from exc

    # Inspect path passively — never launch product login flows.
    inspect_result = adapter.inspect(installation.location_ref)
    if inspect_result.get("status") == "failed":
        raise DiscoveryRegistryError("inspect_failed", str(inspect_result.get("detail")))

    matrix = adapter.validate_capabilities(profile_id="pending")
    access = matrix.access_level()
    # Honest: discovery ≠ schedulable. Keep auth unknown unless evidenced.
    auth_status = "unknown"
    config_payload = {
        "adapterId": adapter.adapter_id,
        "adapterVersion": adapter.adapter_version,
        "installationId": installation_id,
        "locationRef": installation.location_ref,
        "environmentId": installation.environment_id,
    }
    config_hash = hashlib.sha256(
        json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    profile = AgentProfileRow(
        id=f"aprof_{uuid.uuid4().hex[:16]}",
        installation_id=installation_id,
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        config_version="1",
        config_hash=config_hash,
        auth_status=auth_status,
        access_level=access,
        last_validation_id=None,
        secret_ref=None,
        created_at=datetime.now(UTC),
    )
    session.add(profile)
    session.flush()

    results = [
        CapabilityResult(
            capability=name,
            support=support,  # type: ignore[arg-type]
            limits={},
            evidence_refs=[],
        )
        for name, support in matrix.capabilities.items()
    ]
    check = CapabilityCheckRow(
        id=f"achk_{uuid.uuid4().hex[:16]}",
        profile_id=profile.id,
        product_version=installation.product_version,
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        environment_id=installation.environment_id,
        checked_at=datetime.now(UTC),
        valid_until=None,
        results=[r.model_dump(mode="json") for r in results],
        evidence_refs=list(matrix.evidence_refs),
        created_at=datetime.now(UTC),
    )
    session.add(check)
    profile.last_validation_id = check.id
    session.flush()

    return (
        AgentProfile(
            profile_id=profile.id,
            installation_id=profile.installation_id,
            adapter_id=profile.adapter_id,
            adapter_version=profile.adapter_version,
            config_version=profile.config_version,
            config_hash=profile.config_hash,
            auth_status=profile.auth_status,  # type: ignore[arg-type]
            access_level=profile.access_level,  # type: ignore[arg-type]
            last_validation_id=profile.last_validation_id,
            secret_ref=None,
        ),
        CapabilityCheck(
            check_id=check.id,
            profile_id=profile.id,
            product_version=check.product_version,
            adapter_id=check.adapter_id,
            adapter_version=check.adapter_version,
            environment_id=check.environment_id,
            checked_at=check.checked_at,
            valid_until=check.valid_until,
            results=results,
            evidence_refs=[str(x) for x in (check.evidence_refs or [])],
        ),
    )
