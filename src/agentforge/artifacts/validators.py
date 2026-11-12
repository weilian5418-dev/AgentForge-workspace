"""Trusted ValidatorRegistry — Agent self-reported pass is never enough (D06)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from agentforge.artifacts.store import ArtifactStore


@dataclass(frozen=True)
class ValidatorSpec:
    validator_id: str
    version: str
    description: str


@dataclass
class ValidatorOutcome:
    status: str  # passed | failed | not_run
    message: str
    evidence_refs: list[str]


class Validator(Protocol):
    spec: ValidatorSpec

    def run(
        self,
        *,
        store: ArtifactStore,
        content_hash: str,
        meta: dict[str, Any],
    ) -> ValidatorOutcome: ...


class Sha256PresenceValidator:
    spec = ValidatorSpec(
        validator_id="sha256_presence",
        version="1",
        description="Object exists in immutable store and matches content hash",
    )

    def run(
        self,
        *,
        store: ArtifactStore,
        content_hash: str,
        meta: dict[str, Any],
    ) -> ValidatorOutcome:
        _ = meta
        if not store.has(content_hash):
            return ValidatorOutcome("failed", "object missing from store", [])
        try:
            data = store.get_bytes(content_hash)
        except Exception as exc:  # noqa: BLE001
            return ValidatorOutcome("failed", f"read failed: {type(exc).__name__}", [])
        digest = hashlib.sha256(data).hexdigest()
        if digest != content_hash:
            return ValidatorOutcome("failed", "hash mismatch", [])
        return ValidatorOutcome(
            "passed",
            "content hash verified",
            [f"content:{content_hash}"],
        )


class Utf8TextValidator:
    spec = ValidatorSpec(
        validator_id="utf8_text",
        version="1",
        description="Content is valid UTF-8 text",
    )

    def run(
        self,
        *,
        store: ArtifactStore,
        content_hash: str,
        meta: dict[str, Any],
    ) -> ValidatorOutcome:
        _ = meta
        if not store.has(content_hash):
            return ValidatorOutcome("not_run", "object missing; cannot validate", [])
        data = store.get_bytes(content_hash)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return ValidatorOutcome("failed", "not valid utf-8", [f"content:{content_hash}"])
        return ValidatorOutcome("passed", "utf-8 ok", [f"content:{content_hash}"])


class ValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, validator: Validator) -> None:
        self._validators[validator.spec.validator_id] = validator

    def get(self, validator_id: str) -> Validator:
        if validator_id not in self._validators:
            raise KeyError(validator_id)
        return self._validators[validator_id]

    def list_ids(self) -> list[str]:
        return sorted(self._validators)


def default_registry() -> ValidatorRegistry:
    registry = ValidatorRegistry()
    registry.register(Sha256PresenceValidator())
    registry.register(Utf8TextValidator())
    return registry
