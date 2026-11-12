"""ModelGateway: authorize → reserve → dispatch → settle (sync path, D01)."""

from __future__ import annotations

import getpass
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentforge.contracts.providers import (
    ContextManifest,
    ModelAttempt,
    ModelAuthorizationGrant,
    ModelAuthorizationGrantCreateRequest,
    ModelCall,
    ModelCallCreateRequest,
    NetworkPolicy,
    ProviderCapabilityItem,
    ProviderCreateRequest,
    ProviderProbeRequest,
    ProviderProbeResult,
    ProviderProfile,
    ProviderUpdateRequest,
)
from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError
from agentforge.models.adapters import AdapterError, get_adapter
from agentforge.models.budget import BudgetError, BudgetService
from agentforge.models.credentials import ModelCredentialStore
from agentforge.models.network import NetworkPolicyError, validate_endpoint
from agentforge.storage.models import (
    ContextManifestRow,
    ModelAttemptRow,
    ModelAuthorizationGrantRow,
    ModelCallRow,
    ProviderCapabilityCheckRow,
    ProviderProfileRow,
    UsageRecordRow,
)


class ModelGatewayError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ModelGateway:
    """Sync internal/CLI model calls. Async 202 probe persistence is D02."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.budget = BudgetService(session)
        self.credentials = ModelCredentialStore(settings)

    # --- provider config ---

    def create_provider(self, request: ProviderCreateRequest) -> ProviderProfile:
        policy = request.network_policy or NetworkPolicy(
            network_class="local"
            if "127.0.0.1" in request.base_url or "localhost" in request.base_url
            else "remote"
        )
        try:
            validate_endpoint(request.base_url, policy)
        except NetworkPolicyError as exc:
            raise ModelGatewayError(exc.code, exc.message) from exc
        if request.auth_mode == "none" and request.credential_ref:
            raise ModelGatewayError("invalid_auth", "credentialRef not allowed when authMode=none")
        if request.auth_mode != "none":
            if not request.credential_ref:
                raise ModelGatewayError(
                    "credential_required", "credentialRef required for this authMode"
                )
            if self.credentials.status(request.credential_ref) != "configured":
                raise ModelGatewayError("credential_missing", "Model credential not configured")

        row = ProviderProfileRow(
            id=f"prov_{uuid.uuid4().hex[:16]}",
            adapter_type=request.adapter_type,
            base_url=request.base_url,
            model_id=request.model_id,
            auth_mode=request.auth_mode,
            credential_ref=request.credential_ref,
            config_version=1,
            network_policy=policy.model_dump(mode="json"),
            status="enabled",
            label=request.label,
        )
        self.session.add(row)
        self.session.flush()
        return self._provider_contract(row)

    def update_provider(self, provider_id: str, request: ProviderUpdateRequest) -> ProviderProfile:
        row = self.session.get(ProviderProfileRow, provider_id)
        if row is None:
            raise ModelGatewayError("provider_not_found", "Provider not found")
        if request.label is not None:
            row.label = request.label
        if request.enabled is not None:
            row.status = "enabled" if request.enabled else "disabled"
        if request.model_id is not None:
            row.model_id = request.model_id
            row.config_version += 1
        if request.network_policy is not None:
            try:
                validate_endpoint(row.base_url, request.network_policy)
            except NetworkPolicyError as exc:
                raise ModelGatewayError(exc.code, exc.message) from exc
            row.network_policy = request.network_policy.model_dump(mode="json")
            row.config_version += 1
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return self._provider_contract(row)

    def get_provider(self, provider_id: str) -> ProviderProfile:
        row = self.session.get(ProviderProfileRow, provider_id)
        if row is None:
            raise ModelGatewayError("provider_not_found", "Provider not found")
        return self._provider_contract(row)

    def list_providers(self) -> list[ProviderProfile]:
        rows = self.session.scalars(
            select(ProviderProfileRow).order_by(ProviderProfileRow.created_at)
        )
        return [self._provider_contract(r) for r in rows]

    # --- grants ---

    def create_grant(
        self,
        request: ModelAuthorizationGrantCreateRequest,
        *,
        authorized_user: str | None = None,
    ) -> ModelAuthorizationGrant:
        provider = self.session.get(ProviderProfileRow, request.provider_id)
        if provider is None:
            raise ModelGatewayError("provider_not_found", "Provider not found")
        if provider.status != "enabled":
            raise ModelGatewayError("provider_disabled", "Provider is disabled")
        row = ModelAuthorizationGrantRow(
            id=f"mgr_{uuid.uuid4().hex[:16]}",
            authorized_user=authorized_user or getpass.getuser(),
            provider_id=provider.id,
            provider_config_version=provider.config_version,
            purpose=request.purpose,
            allowed_input_ids=list(request.allowed_input_ids),
            allowed_baseline_ids=list(request.allowed_baseline_ids),
            max_calls=request.max_calls,
            calls_used=0,
            max_output_tokens=request.max_output_tokens,
            budget_account_id=request.budget_account_id,
            grant_version=1,
            status="active",
            expires_at=request.expires_at,
        )
        self.session.add(row)
        self.session.flush()
        return self._grant_contract(row)

    def revoke_grant(self, grant_id: str) -> ModelAuthorizationGrant:
        row = self.session.get(ModelAuthorizationGrantRow, grant_id)
        if row is None:
            raise ModelGatewayError("grant_not_found", "Model authorization grant not found")
        row.status = "revoked"
        row.revoked_at = datetime.now(UTC)
        row.grant_version += 1
        self.session.flush()
        return self._grant_contract(row)

    # --- sync call ---

    def invoke(self, request: ModelCallCreateRequest) -> ModelCall:
        # Idempotency: same key + same hash → original; same key different hash → conflict.
        existing = self.session.scalar(
            select(ModelCallRow).where(ModelCallRow.idempotency_key == request.idempotency_key)
        )
        req_hash = _request_hash(
            {
                "providerId": request.provider_id,
                "grantId": request.grant_id,
                "purpose": request.purpose,
                "messages": request.messages,
                "inputIds": request.input_ids,
                "baselineIds": request.baseline_ids,
                "maxOutputTokens": request.max_output_tokens,
            }
        )
        if existing is not None:
            if existing.request_hash != req_hash:
                raise ModelGatewayError(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency key reused with a different request",
                )
            return self._call_contract(existing)

        provider = self.session.get(ProviderProfileRow, request.provider_id)
        if provider is None or provider.status != "enabled":
            raise ModelGatewayError("provider_not_found", "Provider not available")
        grant = self.session.get(ModelAuthorizationGrantRow, request.grant_id)
        if grant is None:
            raise ModelGatewayError("MODEL_AUTHORIZATION_DENIED", "Grant not found")
        self._assert_grant_usable(grant, provider, request)

        call_id = f"mcall_{uuid.uuid4().hex[:16]}"
        manifest = self._build_manifest(grant, request)
        max_out = min(
            request.max_output_tokens or grant.max_output_tokens,
            grant.max_output_tokens,
        )

        # Money reserve requires trusted pricing for hardCost; otherwise tokens-only.
        reserve_amount = "0"
        reserve_tokens = max_out
        reservation_id = None
        if grant.budget_account_id:
            account = self.budget._lock_account(grant.budget_account_id)  # noqa: SLF001
            if account.hard_cost_enabled:
                raise ModelGatewayError(
                    "HARD_COST_UNAVAILABLE",
                    "Hard cost requires trusted pricing; refusing zero-amount hardCost dispatch",
                )
            try:
                reservation = self.budget.reserve(
                    account_id=grant.budget_account_id,
                    call_id=call_id,
                    amount=reserve_amount,
                    tokens=reserve_tokens,
                )
                reservation_id = reservation.reservation_id
            except BudgetError as exc:
                raise ModelGatewayError(exc.code, exc.message) from exc

        self._consume_grant_call(grant)

        call = ModelCallRow(
            id=call_id,
            provider_id=provider.id,
            provider_config_version=provider.config_version,
            grant_id=grant.id,
            grant_version=grant.grant_version,
            purpose=request.purpose,
            status="reserved" if reservation_id else "authorized",
            settlement_status="reserved" if reservation_id else "none",
            request_hash=req_hash,
            idempotency_key=request.idempotency_key,
            context_manifest_id=manifest.manifest_id,
            reservation_id=reservation_id,
        )
        # F01: persist parent call before child attempt (FK order on real PG).
        self.session.add(call)
        self.session.flush()
        attempt = ModelAttemptRow(
            id=f"matt_{uuid.uuid4().hex[:16]}",
            call_id=call_id,
            attempt_no=1,
            status="prepared",
            request_hash=req_hash,
        )
        self.session.add(attempt)
        self.session.flush()
        # F02: commit dispatch intent before network so crash cannot silently re-send.
        self._commit_intent()

        return self._dispatch_sync(
            provider, call, attempt, request.messages, max_out, reservation_id
        )

    def _dispatch_sync(
        self,
        provider: ProviderProfileRow,
        call: ModelCallRow,
        attempt: ModelAttemptRow,
        messages: list[dict[str, Any]],
        max_out: int,
        reservation_id: str | None,
    ) -> ModelCall:
        # Re-check grant/config at dispatch boundary.
        grant = self.session.get(ModelAuthorizationGrantRow, call.grant_id)
        if (
            grant is None
            or grant.status in {"revoked", "expired"}
            or (grant.expires_at is not None and grant.expires_at <= datetime.now(UTC))
            or provider.status != "enabled"
            or provider.config_version != call.provider_config_version
        ):
            call.status = "failed"
            call.error_code = "MODEL_AUTHORIZATION_DENIED"
            if reservation_id:
                self.budget.release_undispatched(reservation_id, note="revoked_before_dispatch")
                call.settlement_status = "released"
            self.session.flush()
            return self._call_contract(call)

        if grant.grant_version != call.grant_version:
            call.status = "failed"
            call.error_code = "MODEL_AUTHORIZATION_DENIED"
            if reservation_id:
                self.budget.release_undispatched(reservation_id, note="grant_version_changed")
                call.settlement_status = "released"
            self.session.flush()
            return self._call_contract(call)

        attempt.status = "dispatch_started"
        attempt.started_at = datetime.now(UTC)
        call.status = "dispatching"
        self.session.flush()

        policy = NetworkPolicy.model_validate(provider.network_policy or {})
        auth_header = self._auth_header(provider)
        adapter = get_adapter(provider.adapter_type)
        try:
            call.status = "receiving"
            attempt.status = "receiving"
            base_url, model_id = provider.base_url, provider.model_id
            self.session.flush()
            self._commit_intent()
            result = adapter.complete(
                base_url=base_url,
                model_id=model_id,
                messages=messages,
                max_output_tokens=max_out,
                auth_header=auth_header,
                policy=policy,
            )
        except AdapterError as exc:
            attempt.status = "unknown" if exc.retryable else "failed"
            attempt.error_code = exc.code
            attempt.finished_at = datetime.now(UTC)
            call.error_code = exc.code
            if exc.retryable:
                call.status = "unknown"
                if reservation_id:
                    self.budget.mark_unresolved(reservation_id, note=exc.message)
                    call.settlement_status = "unresolved"
            else:
                call.status = "failed"
                if reservation_id:
                    # Auth/param errors: if dispatch_started already set, keep unresolved
                    # unless we can prove no bytes left the process — after dispatch_started
                    # we mark unresolved for network-ish failures; for AUTH we still unknown-safe.
                    self.budget.mark_unresolved(reservation_id, note=exc.message)
                    call.settlement_status = "unresolved"
            self.session.flush()
            return self._call_contract(call)

        # Tool calls normalized but never executed.
        _ = result.tool_calls

        attempt.status = "succeeded"
        attempt.provider_request_id = result.provider_request_id
        attempt.finished_at = datetime.now(UTC)
        call.status = "succeeded"
        call.result_text = result.text[:200_000]
        call.updated_at = datetime.now(UTC)

        usage_row = UsageRecordRow(
            id=f"usage_{uuid.uuid4().hex[:16]}",
            call_id=call.id,
            attempt_id=attempt.id,
            source=result.usage.source,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            raw_provider_usage=result.usage.raw,
        )
        self.session.add(usage_row)

        if reservation_id:
            tokens = result.usage.total_tokens or result.usage.output_tokens or 0
            if result.usage.source == "unknown":
                self.budget.mark_unresolved(reservation_id, note="usage_unknown")
                call.settlement_status = "unresolved"
            else:
                # Token usage known ≠ monetary cost known. Soft/token accounts may
                # settle amount "0"; hardCost was refused at reserve time (F04).
                self.budget.settle(
                    reservation_id=reservation_id,
                    actual_amount="0",
                    actual_tokens=int(tokens),
                    note="provider_usage_tokens_only_cost_unknown",
                )
                call.settlement_status = "settled"
        self.session.flush()
        return self._call_contract(call)

    def probe(
        self,
        provider_id: str,
        request: ProviderProbeRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ProviderProbeResult:
        if not request.authorize_network:
            return ProviderProbeResult(
                provider_id=provider_id,
                probe_id=f"probe_{uuid.uuid4().hex[:12]}",
                status="blocked",
                message="Network probe not authorized",
                probed_at=datetime.now(UTC),
            )
        provider = self.session.get(ProviderProfileRow, provider_id)
        if provider is None:
            raise ModelGatewayError("provider_not_found", "Provider not found")
        # Synthetic no-secret probe; consumes grant+budget.
        # Callers that own an operationId must pass a stable idempotency key (F02).
        call = self.invoke(
            ModelCallCreateRequest.model_validate(
                {
                    "providerId": provider_id,
                    "grantId": request.grant_id,
                    "purpose": "capability_probe",
                    "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
                    "idempotencyKey": idempotency_key or f"probe_{uuid.uuid4().hex}",
                    "maxOutputTokens": 16,
                    "stream": False,
                }
            )
        )
        caps: list[ProviderCapabilityItem] = []
        status: str
        if call.status == "succeeded":
            status = "succeeded"
            caps.append(ProviderCapabilityItem(capability="text_completion", status="supported"))
            caps.append(
                ProviderCapabilityItem(
                    capability="streaming",
                    status="unknown",
                    detail="stream not exercised in sync probe",
                )
            )
            caps.append(
                ProviderCapabilityItem(
                    capability="tools",
                    status="unsupported" if not request.probe_tools else "unknown",
                    detail="tools are never executed by the gateway",
                )
            )
        elif call.status == "unknown":
            status = "failed"
            caps.append(ProviderCapabilityItem(capability="text_completion", status="unknown"))
        else:
            status = "failed"
            caps.append(
                ProviderCapabilityItem(
                    capability="text_completion",
                    status="failed",
                    detail=call.error_code,
                )
            )
        check = ProviderCapabilityCheckRow(
            id=f"pcap_{uuid.uuid4().hex[:16]}",
            provider_id=provider.id,
            provider_config_version=provider.config_version,
            adapter_type=provider.adapter_type,
            model_id=provider.model_id,
            checked_at=datetime.now(UTC),
            results=[c.model_dump(mode="json") for c in caps],
            evidence_refs=[call.call_id],
        )
        self.session.add(check)
        self.session.flush()
        return ProviderProbeResult(
            provider_id=provider_id,
            probe_id=check.id,
            call_id=call.call_id,
            status=status,  # type: ignore[arg-type]
            capabilities=caps,
            message=f"probe status={status}",
            probed_at=check.checked_at,
        )

    def get_call(self, call_id: str) -> ModelCall:
        row = self.session.get(ModelCallRow, call_id)
        if row is None:
            raise ModelGatewayError("call_not_found", "Model call not found")
        return self._call_contract(row)

    # --- helpers ---

    def _assert_grant_usable(
        self,
        grant: ModelAuthorizationGrantRow,
        provider: ProviderProfileRow,
        request: ModelCallCreateRequest,
    ) -> None:
        if grant.status != "active":
            raise ModelGatewayError("MODEL_AUTHORIZATION_DENIED", f"Grant status={grant.status}")
        if grant.provider_id != provider.id:
            raise ModelGatewayError("MODEL_AUTHORIZATION_DENIED", "Grant provider mismatch")
        if grant.provider_config_version != provider.config_version:
            raise ModelGatewayError(
                "MODEL_AUTHORIZATION_DENIED",
                "Grant providerConfigVersion does not match current provider config",
            )
        if grant.expires_at and grant.expires_at <= datetime.now(UTC):
            grant.status = "expired"
            raise ModelGatewayError("MODEL_AUTHORIZATION_DENIED", "Grant expired")
        if grant.calls_used >= grant.max_calls:
            grant.status = "exhausted"
            raise ModelGatewayError("MODEL_AUTHORIZATION_DENIED", "Grant call limit reached")
        if request.purpose != grant.purpose:
            raise ModelGatewayError("MODEL_AUTHORIZATION_DENIED", "Purpose not authorized")
        if (
            request.max_output_tokens is not None
            and request.max_output_tokens > grant.max_output_tokens
        ):
            raise ModelGatewayError(
                "MODEL_AUTHORIZATION_DENIED",
                "maxOutputTokens exceeds grant max_output_tokens",
            )
        # Empty allow-lists mean no project inputs/baselines may be attached (F05).
        allowed_inputs = set(grant.allowed_input_ids or [])
        if request.input_ids:
            if not allowed_inputs:
                raise ModelGatewayError(
                    "CONTEXT_MANIFEST_MISMATCH",
                    "Grant allows no project inputs but request attached inputIds",
                )
            for input_id in request.input_ids:
                if input_id not in allowed_inputs:
                    raise ModelGatewayError(
                        "CONTEXT_MANIFEST_MISMATCH", f"Input {input_id} not in grant"
                    )
        allowed_baselines = set(grant.allowed_baseline_ids or [])
        if request.baseline_ids:
            if not allowed_baselines:
                raise ModelGatewayError(
                    "CONTEXT_MANIFEST_MISMATCH",
                    "Grant allows no baselines but request attached baselineIds",
                )
            for baseline_id in request.baseline_ids:
                if baseline_id not in allowed_baselines:
                    raise ModelGatewayError(
                        "CONTEXT_MANIFEST_MISMATCH", f"Baseline {baseline_id} not in grant"
                    )

    def _consume_grant_call(self, grant: ModelAuthorizationGrantRow) -> None:
        """Atomically consume one grant call slot (CAS on calls_used)."""
        expected = grant.calls_used
        new_used = expected + 1
        new_status = "exhausted" if new_used >= grant.max_calls else grant.status
        try:
            result = self.session.execute(
                update(ModelAuthorizationGrantRow)
                .where(
                    ModelAuthorizationGrantRow.id == grant.id,
                    ModelAuthorizationGrantRow.status == "active",
                    ModelAuthorizationGrantRow.calls_used == expected,
                    ModelAuthorizationGrantRow.calls_used < ModelAuthorizationGrantRow.max_calls,
                )
                .values(calls_used=new_used, status=new_status)
            )
            if getattr(result, "rowcount", 1) == 0:
                raise ModelGatewayError(
                    "MODEL_AUTHORIZATION_DENIED", "Grant call limit race; retry"
                )
        except ModelGatewayError:
            raise
        except Exception:
            if grant.status != "active" or grant.calls_used != expected:
                raise ModelGatewayError(
                    "MODEL_AUTHORIZATION_DENIED", "Grant call limit race; retry"
                ) from None
            if grant.calls_used >= grant.max_calls:
                raise ModelGatewayError(
                    "MODEL_AUTHORIZATION_DENIED", "Grant call limit reached"
                ) from None
        grant.calls_used = new_used
        grant.status = new_status

    def _commit_intent(self) -> None:
        """A failed commit must prevent external dispatch, never silently fall back."""
        self.session.commit()

    def _build_manifest(
        self, grant: ModelAuthorizationGrantRow, request: ModelCallCreateRequest
    ) -> ContextManifest:
        hashes = [
            hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
            for m in request.messages
        ]
        row = ContextManifestRow(
            id=f"ctx_{uuid.uuid4().hex[:16]}",
            grant_id=grant.id,
            approved_input_ids=list(request.input_ids),
            approved_baseline_ids=list(request.baseline_ids),
            fragment_refs=[f"message[{i}]" for i in range(len(request.messages))],
            content_hashes=hashes,
            summary_strategy="refs_only",
            content_version=1,
        )
        self.session.add(row)
        self.session.flush()
        return ContextManifest(
            manifest_id=row.id,
            grant_id=grant.id,
            approved_input_ids=list(request.input_ids),
            approved_baseline_ids=list(request.baseline_ids),
            fragment_refs=list(row.fragment_refs),
            content_hashes=hashes,
            summary_strategy="refs_only",
            content_version=1,
        )

    def _auth_header(self, provider: ProviderProfileRow) -> dict[str, str] | None:
        if provider.auth_mode == "none":
            return None
        try:
            secret = self.credentials.resolve(provider.credential_ref or "")
        except CredentialError as exc:
            raise ModelGatewayError("credential_missing", str(exc)) from exc
        if provider.auth_mode == "bearer":
            return {"Authorization": f"Bearer {secret}"}
        if provider.auth_mode == "api_key_header":
            return {"Authorization": f"Bearer {secret}"}
        raise ModelGatewayError("invalid_auth", f"Unsupported authMode {provider.auth_mode}")

    def _provider_contract(self, row: ProviderProfileRow) -> ProviderProfile:
        policy = NetworkPolicy.model_validate(row.network_policy or {})
        return ProviderProfile(
            provider_id=row.id,
            adapter_type=row.adapter_type,  # type: ignore[arg-type]
            base_url=row.base_url,
            model_id=row.model_id,
            auth_mode=row.auth_mode,  # type: ignore[arg-type]
            credential_ref=row.credential_ref,
            credential_status=self.credentials.status(row.credential_ref),  # type: ignore[arg-type]
            config_version=row.config_version,
            network_policy=policy,
            status=row.status,  # type: ignore[arg-type]
            label=row.label,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _grant_contract(self, row: ModelAuthorizationGrantRow) -> ModelAuthorizationGrant:
        return ModelAuthorizationGrant(
            grant_id=row.id,
            authorized_user=row.authorized_user,
            provider_id=row.provider_id,
            provider_config_version=row.provider_config_version,
            purpose=row.purpose,
            allowed_input_ids=[str(x) for x in (row.allowed_input_ids or [])],
            allowed_baseline_ids=[str(x) for x in (row.allowed_baseline_ids or [])],
            max_calls=row.max_calls,
            calls_used=row.calls_used,
            max_output_tokens=row.max_output_tokens,
            budget_account_id=row.budget_account_id,
            grant_version=row.grant_version,
            status=row.status,  # type: ignore[arg-type]
            expires_at=row.expires_at,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
        )

    def _call_contract(self, row: ModelCallRow) -> ModelCall:
        attempts = list(
            self.session.scalars(
                select(ModelAttemptRow)
                .where(ModelAttemptRow.call_id == row.id)
                .order_by(ModelAttemptRow.attempt_no)
            )
        )
        return ModelCall(
            call_id=row.id,
            provider_id=row.provider_id,
            provider_config_version=row.provider_config_version,
            grant_id=row.grant_id,
            grant_version=row.grant_version,
            purpose=row.purpose,
            status=row.status,  # type: ignore[arg-type]
            settlement_status=row.settlement_status,  # type: ignore[arg-type]
            request_hash=row.request_hash,
            idempotency_key=row.idempotency_key,
            context_manifest_id=row.context_manifest_id,
            reservation_id=row.reservation_id,
            result_text=row.result_text,
            error_code=row.error_code,
            attempts=[
                ModelAttempt(
                    attempt_id=a.id,
                    call_id=a.call_id,
                    attempt_no=a.attempt_no,
                    status=a.status,  # type: ignore[arg-type]
                    request_hash=a.request_hash,
                    provider_request_id=a.provider_request_id,
                    error_code=a.error_code,
                    started_at=a.started_at,
                    finished_at=a.finished_at,
                )
                for a in attempts
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
