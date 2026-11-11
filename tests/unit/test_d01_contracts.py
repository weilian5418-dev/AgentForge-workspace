"""D01 contract shapes: decimal money, provider profile, grant-only fields."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.contracts.providers import (
    DecimalMoney,
    ModelCallCreateRequest,
    ProviderCreateRequest,
    ProviderProfile,
)


def test_decimal_money_is_string_not_float() -> None:
    money = DecimalMoney.model_validate(
        {"amount": "1.25", "currency": "usd", "estimated": True}
    )
    assert money.currency == "USD"
    assert isinstance(money.amount, str)
    with pytest.raises(ValidationError):
        DecimalMoney.model_validate(
            {"amount": 1.25, "currency": "USD", "estimated": True}
        )


def test_provider_create_requires_adapter_and_model_id() -> None:
    req = ProviderCreateRequest.model_validate(
        {
            "adapterType": "ollama",
            "baseUrl": "http://127.0.0.1:11434",
            "modelId": "llama3.2",
            "authMode": "none",
        }
    )
    assert req.adapter_type == "ollama"
    with pytest.raises(ValidationError):
        ProviderCreateRequest.model_validate(
            {
                "baseUrl": "http://127.0.0.1:11434",
                "model": "legacy",
                "credentialRef": "mcred_x",
            }
        )


def test_model_call_requires_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        ModelCallCreateRequest.model_validate(
            {
                "providerId": "prov_x",
                "grantId": "mgr_x",
                "purpose": "probe",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )


def test_provider_profile_rejects_secret_fields() -> None:
    with pytest.raises(ValidationError):
        ProviderProfile.model_validate(
            {
                "providerId": "prov_x",
                "adapterType": "ollama",
                "baseUrl": "http://127.0.0.1:11434",
                "modelId": "m",
                "authMode": "none",
                "credentialStatus": "not_required",
                "configVersion": 1,
                "networkPolicy": {"networkClass": "local"},
                "status": "enabled",
                "createdAt": "2026-11-11T00:00:00Z",
                "updatedAt": "2026-11-11T00:00:00Z",
                "apiKey": "sk-leak",
            }
        )
