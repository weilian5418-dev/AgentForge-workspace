"""Delivery manifests and user acceptance (D12)."""

from agentforge.delivery.acceptance import decide_acceptance, validate_member_dependency_closure
from agentforge.delivery.manifest import (
    build_delivery_manifest,
    canonical_manifest_bytes,
    compute_manifest_hash,
    publish_manifest,
)

__all__ = [
    "build_delivery_manifest",
    "canonical_manifest_bytes",
    "compute_manifest_hash",
    "decide_acceptance",
    "publish_manifest",
    "validate_member_dependency_closure",
]
