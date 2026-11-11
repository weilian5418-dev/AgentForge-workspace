"""Model provider, authorization, call ledger, and budget contracts (D01 / BE-05+06)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import OpaqueId, Sha256Hex, ShortText, UtcDateTime

AdapterType = Literal["openai_compatible", "ollama"]
AuthMode = Literal["none", "bearer", "api_key_header"]
NetworkClass = Literal["local", "private", "remote"]
ProviderStatus = Literal["enabled", "disabled"]
GrantStatus = Literal["active", "revoked", "exhausted", "expired"]
CallStatus = Literal[
    "accepted",
    "authorized",
    "reserved",
    "dispatching",
    "receiving",
    "succeeded",
    "failed",
    "canceled",
    "unknown",
    "awaiting_authorization",
]
AttemptStatus = Literal[
    "prepared",
    "dispatch_started",
    "receiving",
    "succeeded",
    "failed",
    "canceled",
    "unknown",
]
ReservationStatus = Literal["active", "settled", "released", "unresolved"]
UsageSource = Literal["provider_reported", "platform_estimated", "unknown"]
SettlementKind = Literal["settle", "adjust", "mark_unresolved"]
CapabilityItemStatus = Literal["supported", "unsupported", "unknown", "failed"]
DecimalStr = Annotated[str, Field(pattern=r"^(0|[1-9]\d*)(\.\d{1,8})?$")]


def parse_decimal_str(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal string") from exc
    if amount < 0:
        raise ValueError("amount must be >= 0")
    return amount


class DecimalMoney(ContractModel):
    """Exact money amount as decimal string — never float-accumulate fees."""

    amount: DecimalStr
    currency: str = Field(min_length=3, max_length=8)
    estimated: bool = Field(strict=True)
    price_version: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("amount")
    @classmethod
    def amount_ok(cls, value: str) -> str:
        parse_decimal_str(value)
        return value


class NetworkPolicy(ContractModel):
    network_class: NetworkClass = "remote"
    allow_redirects: bool = Field(default=False, strict=True)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=120, strict=True)
    total_timeout_seconds: int = Field(default=120, ge=1, le=3600, strict=True)
    proxy_url: ShortText | None = None
    tls_ca_bundle_ref: ShortText | None = None


class ProviderCreateRequest(ApiInputModel):
    adapter_type: AdapterType
    base_url: ShortText
    model_id: ShortText
    auth_mode: AuthMode = "none"
    credential_ref: OpaqueId | None = None
    label: str | None = Field(default=None, max_length=256)
    network_policy: NetworkPolicy | None = None

    @field_validator("base_url")
    @classmethod
    def http_url(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("baseUrl must be an http(s) URL")
        lowered = value.lower()
        if any(token in lowered for token in ("@", "password", "token=", "api_key=")):
            raise ValueError("baseUrl must not embed credentials")
        return value


class ProviderUpdateRequest(ApiInputModel):
    label: str | None = Field(default=None, max_length=256)
    enabled: bool | None = Field(default=None, strict=True)
    network_policy: NetworkPolicy | None = None
    model_id: ShortText | None = None


class ProviderCredentialRegisterRequest(ApiInputModel):
    """Registers a model credential secret; response never echoes the secret."""

    label: str | None = Field(default=None, max_length=256)
    secret: str = Field(min_length=1, max_length=4096)


class ProviderCredentialRefResponse(ContractModel):
    credential_ref: OpaqueId
    status: Literal["configured", "revoked"]
    label: str | None = None
    created_at: UtcDateTime


class ProviderProfile(ContractModel):
    provider_id: OpaqueId
    adapter_type: AdapterType
    base_url: ShortText
    model_id: ShortText
    auth_mode: AuthMode
    credential_ref: OpaqueId | None = None
    credential_status: Literal["configured", "missing", "revoked", "not_required"]
    config_version: int = Field(ge=1, strict=True)
    network_policy: NetworkPolicy
    status: ProviderStatus
    label: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


ProviderResponse = ProviderProfile


class ModelAuthorizationGrantCreateRequest(ApiInputModel):
    provider_id: OpaqueId
    purpose: ShortText
    max_calls: int = Field(ge=1, le=10_000, strict=True)
    expires_at: UtcDateTime | None = None
    allowed_input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    allowed_baseline_ids: list[OpaqueId] = Field(default_factory=list, max_length=16)
    max_output_tokens: int = Field(default=2048, ge=1, le=128_000, strict=True)
    budget_account_id: OpaqueId | None = None


class ModelAuthorizationGrant(ContractModel):
    grant_id: OpaqueId
    authorized_user: ShortText
    provider_id: OpaqueId
    provider_config_version: int = Field(ge=1, strict=True)
    purpose: ShortText
    allowed_input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    allowed_baseline_ids: list[OpaqueId] = Field(default_factory=list, max_length=16)
    max_calls: int = Field(ge=1, strict=True)
    calls_used: int = Field(ge=0, strict=True)
    max_output_tokens: int = Field(ge=1, strict=True)
    budget_account_id: OpaqueId | None = None
    grant_version: int = Field(ge=1, strict=True)
    status: GrantStatus
    expires_at: UtcDateTime | None = None
    created_at: UtcDateTime
    revoked_at: UtcDateTime | None = None


class ContextManifest(ContractModel):
    manifest_id: OpaqueId
    grant_id: OpaqueId
    approved_input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    approved_baseline_ids: list[OpaqueId] = Field(default_factory=list, max_length=16)
    fragment_refs: list[ShortText] = Field(default_factory=list, max_length=128)
    content_hashes: list[Sha256Hex] = Field(default_factory=list, max_length=128)
    summary_strategy: ShortText = "refs_only"
    content_version: int = Field(ge=1, strict=True)
    # Plaintext is never persisted by default.


class BudgetAccountCreateRequest(ApiInputModel):
    scope_type: Literal["run", "provider", "global", "call"]
    scope_id: OpaqueId
    currency: str = Field(min_length=3, max_length=8)
    soft_limit: DecimalStr | None = None
    hard_limit: DecimalStr | None = None
    token_limit: int | None = Field(default=None, ge=1, strict=True)
    hard_cost_enabled: bool = Field(default=False, strict=True)
    price_version: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str) -> str:
        return value.upper()


class BudgetAccount(ContractModel):
    account_id: OpaqueId
    scope_type: ShortText
    scope_id: OpaqueId
    currency: str
    soft_limit: DecimalStr | None = None
    hard_limit: DecimalStr | None = None
    token_limit: int | None = None
    settled_amount: DecimalStr
    reserved_amount: DecimalStr
    settled_tokens: int = Field(ge=0, strict=True)
    reserved_tokens: int = Field(ge=0, strict=True)
    hard_cost_enabled: bool = Field(strict=True)
    price_version: str | None = None
    account_version: int = Field(ge=1, strict=True)
    status: Literal["open", "blocked", "closed"]
    created_at: UtcDateTime


class BudgetReservation(ContractModel):
    reservation_id: OpaqueId
    account_id: OpaqueId
    call_id: OpaqueId
    amount: DecimalStr
    tokens: int = Field(ge=0, strict=True)
    status: ReservationStatus
    account_version_at_reserve: int = Field(ge=1, strict=True)
    created_at: UtcDateTime
    resolved_at: UtcDateTime | None = None


class UsageRecord(ContractModel):
    usage_id: OpaqueId
    call_id: OpaqueId
    attempt_id: OpaqueId | None = None
    source: UsageSource
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    total_tokens: int | None = Field(default=None, ge=0, strict=True)
    cost: DecimalMoney | None = None
    raw_provider_usage: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDateTime


class SettlementRecord(ContractModel):
    settlement_id: OpaqueId
    reservation_id: OpaqueId
    kind: SettlementKind
    amount: DecimalStr
    tokens: int = Field(ge=0, strict=True)
    note: ShortText | None = None
    created_at: UtcDateTime


class ModelCallCreateRequest(ApiInputModel):
    provider_id: OpaqueId
    grant_id: OpaqueId
    purpose: ShortText
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    baseline_ids: list[OpaqueId] = Field(default_factory=list, max_length=16)
    idempotency_key: OpaqueId
    max_output_tokens: int | None = Field(default=None, ge=1, le=128_000, strict=True)
    stream: bool = Field(default=False, strict=True)


class ModelAttempt(ContractModel):
    attempt_id: OpaqueId
    call_id: OpaqueId
    attempt_no: int = Field(ge=1, strict=True)
    status: AttemptStatus
    request_hash: Sha256Hex
    provider_request_id: ShortText | None = None
    error_code: ShortText | None = None
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None


class ModelCall(ContractModel):
    call_id: OpaqueId
    provider_id: OpaqueId
    provider_config_version: int = Field(ge=1, strict=True)
    grant_id: OpaqueId
    grant_version: int = Field(ge=1, strict=True)
    purpose: ShortText
    status: CallStatus
    settlement_status: Literal["none", "reserved", "settled", "unresolved", "released"]
    request_hash: Sha256Hex
    idempotency_key: OpaqueId
    context_manifest_id: OpaqueId | None = None
    reservation_id: OpaqueId | None = None
    result_text: str | None = Field(default=None, max_length=200_000)
    error_code: ShortText | None = None
    attempts: list[ModelAttempt] = Field(default_factory=list, max_length=32)
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ProviderProbeRequest(ApiInputModel):
    authorize_network: bool = Field(strict=True)
    grant_id: OpaqueId
    probe_tools: bool = Field(default=False, strict=True)
    # D02: async=true returns 202 + durable operation; sync remains D01 path.
    async_dispatch: bool = Field(default=False, strict=True)


class ProviderCapabilityItem(ContractModel):
    capability: ShortText
    status: CapabilityItemStatus
    detail: ShortText | None = None


class ProviderProbeResult(ContractModel):
    provider_id: OpaqueId
    probe_id: OpaqueId
    call_id: OpaqueId | None = None
    status: Literal["succeeded", "failed", "blocked"]
    capabilities: list[ProviderCapabilityItem] = Field(default_factory=list, max_length=64)
    message: ShortText
    probed_at: UtcDateTime


# Legacy create shape accepted only via adapter layer mapping in service (not public).
class LegacyProviderCreateRequest(ApiInputModel):
    base_url: ShortText
    model: ShortText
    credential_ref: OpaqueId
    label: str | None = Field(default=None, max_length=256)
