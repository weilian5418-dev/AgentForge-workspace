"""Load versioned capability evidence; unknown is never executable."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.storage.models import AgentInstallationRow, AgentProfileRow, CapabilityCheckRow


def require_profile(
    session: Session, profile_id: str, required: set[str], *, expected_version: str | None = None
) -> Any:
    profile = session.get(AgentProfileRow, profile_id, with_for_update=True, populate_existing=True)
    if profile is None:
        raise InstanceError("profile_not_found", "Agent profile does not exist")
    if profile.auth_status != "authenticated":
        raise InstanceError("auth_invalid", "Agent authentication is not verified")
    if expected_version is not None and profile.config_version != expected_version:
        raise InstanceError("profile_config_changed", "Profile changed since confirmation")
    installation = session.get(AgentInstallationRow, profile.installation_id)
    check = (
        session.get(
            CapabilityCheckRow,
            profile.last_validation_id,
            with_for_update=True,
            populate_existing=True,
        )
        if profile.last_validation_id
        else None
    )
    now = datetime.now(UTC)
    if (
        check is None
        or installation is None
        or check.profile_id != profile.id
        or check.adapter_id != profile.adapter_id
        or check.adapter_version != profile.adapter_version
        or check.environment_id != installation.environment_id
        or check.product_version != installation.product_version
        or check.checked_at is None
        or check.checked_at > now
        or now - check.checked_at > timedelta(hours=24)
        or (check.valid_until is not None and check.valid_until <= now)
    ):
        raise InstanceError(
            "capability_unverified", "Capability evidence missing, stale or mismatched"
        )
    supported = {r.get("capability") for r in check.results if r.get("support") == "supported"}
    if not required <= supported:
        raise InstanceError("capability_mismatch", "Required capability is unsupported or unknown")
    # Evidence must bind configuration hash, not just the unchanged product version.
    if f"config:{profile.config_hash}" not in (check.evidence_refs or []):
        raise InstanceError(
            "capability_unverified", "Validation does not bind current configuration hash"
        )
    return profile, installation, check
