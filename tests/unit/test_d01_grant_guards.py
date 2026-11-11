"""F05 grant allow-list / config version / output cap guards."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentforge.contracts.providers import ModelCallCreateRequest
from agentforge.core.config import Settings
from agentforge.models.gateway import ModelGateway, ModelGatewayError
from agentforge.storage.models import ModelAuthorizationGrantRow, ProviderProfileRow


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        if getattr(row, "id", None):
            self.store[(type(row), row.id)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        return None

    def execute(self, stmt: Any) -> Any:
        raise RuntimeError("no SQL in mem session")


def _provider(session: _MemSession, *, version: int = 1) -> ProviderProfileRow:
    row = ProviderProfileRow(
        id="prov_1",
        adapter_type="ollama",
        base_url="http://127.0.0.1:9",
        model_id="m",
        auth_mode="none",
        config_version=version,
        network_policy={"networkClass": "local", "allowRedirects": False},
        status="enabled",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(row)
    return row


def _grant(session: _MemSession, **kwargs: Any) -> ModelAuthorizationGrantRow:
    defaults = {
        "id": "mgr_1",
        "authorized_user": "test",
        "provider_id": "prov_1",
        "provider_config_version": 1,
        "purpose": "capability_probe",
        "allowed_input_ids": [],
        "allowed_baseline_ids": [],
        "max_calls": 3,
        "calls_used": 0,
        "max_output_tokens": 16,
        "grant_version": 1,
        "status": "active",
    }
    defaults.update(kwargs)
    row = ModelAuthorizationGrantRow(**defaults)
    session.add(row)
    return row


def test_empty_allow_list_rejects_attached_inputs() -> None:
    session = _MemSession()
    _provider(session)
    grant = _grant(session)
    gw = ModelGateway(session, Settings())  # type: ignore[arg-type]
    req = ModelCallCreateRequest.model_validate(
        {
            "providerId": "prov_1",
            "grantId": "mgr_1",
            "purpose": "capability_probe",
            "messages": [{"role": "user", "content": "x"}],
            "inputIds": ["inp_unauthorized"],
            "idempotencyKey": "k1",
            "maxOutputTokens": 8,
        }
    )
    with pytest.raises(ModelGatewayError) as exc:
        gw._assert_grant_usable(grant, session.get(ProviderProfileRow, "prov_1"), req)  # type: ignore[arg-type]
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_stale_provider_config_version_denied() -> None:
    session = _MemSession()
    provider = _provider(session, version=2)
    grant = _grant(session, provider_config_version=1)
    gw = ModelGateway(session, Settings())  # type: ignore[arg-type]
    req = ModelCallCreateRequest.model_validate(
        {
            "providerId": "prov_1",
            "grantId": "mgr_1",
            "purpose": "capability_probe",
            "messages": [{"role": "user", "content": "x"}],
            "idempotencyKey": "k2",
        }
    )
    with pytest.raises(ModelGatewayError) as exc:
        gw._assert_grant_usable(grant, provider, req)
    assert exc.value.code == "MODEL_AUTHORIZATION_DENIED"
    assert "providerConfigVersion" in exc.value.message


def test_output_token_cap_enforced() -> None:
    session = _MemSession()
    provider = _provider(session)
    grant = _grant(session, max_output_tokens=16)
    gw = ModelGateway(session, Settings())  # type: ignore[arg-type]
    req = ModelCallCreateRequest.model_validate(
        {
            "providerId": "prov_1",
            "grantId": "mgr_1",
            "purpose": "capability_probe",
            "messages": [{"role": "user", "content": "x"}],
            "idempotencyKey": "k3",
            "maxOutputTokens": 1000,
        }
    )
    with pytest.raises(ModelGatewayError) as exc:
        gw._assert_grant_usable(grant, provider, req)
    assert exc.value.code == "MODEL_AUTHORIZATION_DENIED"
