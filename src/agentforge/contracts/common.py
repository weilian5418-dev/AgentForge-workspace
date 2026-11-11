"""Shared field types and contract-level constraints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    Field,
    StringConstraints,
    field_validator,
)

from agentforge.contracts.base import ApiInputModel, ContractModel

SCHEMA_VERSION = "1.4"

OpaqueId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=16_384)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

ResourceRevision = Annotated[int, Field(ge=1, strict=True)]
PlanVersion = Annotated[int, Field(ge=1, strict=True)]
AssignmentVersion = Annotated[int, Field(ge=1, strict=True)]
ArtifactVersion = Annotated[int, Field(ge=1, strict=True)]
RunSequence = Annotated[int, Field(ge=0, strict=True)]
EventSequence = Annotated[int, Field(ge=1, strict=True)]

RunMode = Literal["auto", "single", "team"]
ExecutionProfile = Literal["container", "host_authorized"]
FailurePolicy = Literal["dependency-aware", "layer-atomic"]
ArtifactKind = Literal["code", "markdown", "pptx"]

# New primary path: draft → preflight → planning → awaiting_confirmation → preparing →
# running → awaiting_acceptance → completed (+ pause/cancel/blocked/failed).
# Legacy values routing / awaiting_approval remain readable during migration only.
RunStatus = Literal[
    "draft",
    "preflight",
    "routing",  # legacy → map to planning
    "planning",
    "awaiting_approval",  # legacy overloaded; map carefully (see LEGACY_RUN_STATUS_MAP)
    "awaiting_confirmation",
    "preparing",
    "running",
    "awaiting_acceptance",
    "completed",
    "pause_requested",
    "paused",
    "blocked",
    "cancel_requested",
    "canceled",
    "failed",
]

# Explicit legacy mapping — do not equate action-auth waits with plan confirmation.
# awaiting_approval = action/native auth wait (legacy name).
# awaiting_confirmation = plan confirmation wait.
# Never auto-map between them without migration-source evidence (see Alembic 0005).
LEGACY_RUN_STATUS_MAP: dict[str, str] = {
    "routing": "planning",
}

WorkPackageStatus = Literal[
    "pending",
    "ready",
    "running",
    "validating",
    "published",
    "repairing",
    "failed",
    "blocked",
    "blocked_dependency",
]

ArtifactState = Literal["candidate", "validating", "published", "rejected", "superseded"]
PlanStatus = Literal["proposed", "approved", "rejected", "superseded"]
CheckStatus = Literal["passed", "failed", "blocked", "not_run"]
FailureClass = Literal["content", "environment", "transient", "permission"]
RoutingModeDecision = Literal["single", "team", "needs_input", "undecided"]


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Timestamps must include a timezone; naive datetimes are rejected")
    return value.astimezone(UTC)


UtcDateTime = Annotated[AwareDatetime, AfterValidator(ensure_utc)]


def normalize_relative_path(value: str) -> str:
    windows = PureWindowsPath(value)
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        windows.drive
        or windows.root
        or path.is_absolute()
        or ".." in path.parts
        or ":" in value
        or "\x00" in value
        or str(path) == "."
        or len(normalized) > 512
    ):
        raise ValueError("Path must be a relative path within an authorized workspace")
    return normalized


RelativePath = Annotated[str, AfterValidator(normalize_relative_path)]


class BudgetLimits(ContractModel):
    max_tokens: int | None = Field(default=None, ge=1, strict=True)
    max_wall_time_seconds: int | None = Field(default=None, ge=1, strict=True)
    max_cost: float | None = Field(default=None, gt=0, strict=True)
    currency: str | None = Field(default=None, min_length=3, max_length=8)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str | None) -> str | None:
        return None if value is None else value.upper()


class BudgetLimitsInput(ApiInputModel):
    """External nested budget input: camelCase aliases only (matches exported schema)."""

    max_tokens: int | None = Field(default=None, ge=1, strict=True)
    max_wall_time_seconds: int | None = Field(default=None, ge=1, strict=True)
    max_cost: float | None = Field(default=None, gt=0, strict=True)
    currency: str | None = Field(default=None, min_length=3, max_length=8)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str | None) -> str | None:
        return None if value is None else value.upper()


class PageCursor(ContractModel):
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=20, ge=1, le=100, strict=True)


class PageInfo(ContractModel):
    next_cursor: str | None = None
    limit: int = Field(ge=1, le=100, strict=True)


class MoneyAmount(ContractModel):
    """Legacy float money field on run budgets.

    New model-call ledgers (D01) use ``DecimalMoney`` with decimal strings.
    Do not float-accumulate fees on the model path.
    """

    amount: float | None = Field(default=None, ge=0, strict=True)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    estimated: bool = Field(strict=True)
    price_version: str | None = Field(default=None, max_length=64)


def dump_contract(model: ContractModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)
