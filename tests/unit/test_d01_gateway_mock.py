"""D01 gateway sync path with mocked adapter (no real network)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from agentforge.contracts.providers import (
    ModelAuthorizationGrantCreateRequest,
    ModelCallCreateRequest,
    NetworkPolicy,
    ProviderCreateRequest,
)
from agentforge.core.config import Settings
from agentforge.models.adapters import NormalizedCompletion, NormalizedUsage
from agentforge.models.gateway import ModelGateway
from agentforge.storage.models import (
    ContextManifestRow,
    ModelAttemptRow,
    ModelAuthorizationGrantRow,
    ModelCallRow,
    ProviderProfileRow,
    UsageRecordRow,
)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        if getattr(row, "id", None):
            self.store[(type(row), row.id)] = row

    def flush(self) -> None:
        now = datetime.now(UTC)
        for row in self.added:
            if hasattr(row, "created_at") and getattr(row, "created_at", None) is None:
                row.created_at = now
            if hasattr(row, "updated_at") and getattr(row, "updated_at", None) is None:
                row.updated_at = now

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        if "model_calls" in text and "idempotency" in text:
            return None
        if "provider_profiles" in text:
            return None
        return None

    def scalars(self, stmt: Any) -> Any:
        class _Result:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def __iter__(self):
                return iter(self._rows)

            def all(self) -> list[Any]:
                return list(self._rows)

        # Return attempts for call contract
        attempts = [r for r in self.added if isinstance(r, ModelAttemptRow)]
        return _Result(attempts)


def test_invoke_success_settles_known_usage(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path / "state")
    session = _MemSession()
    gw = ModelGateway(session, settings)  # type: ignore[arg-type]

    provider = gw.create_provider(
        ProviderCreateRequest.model_validate(
            {
                "adapterType": "openai_compatible",
                "baseUrl": "http://127.0.0.1:9",
                "modelId": "dummy",
                "authMode": "none",
                "networkPolicy": {
                    "networkClass": "local",
                    "allowRedirects": False,
                    "connectTimeoutSeconds": 1,
                    "totalTimeoutSeconds": 2,
                },
            }
        )
    )
    grant = gw.create_grant(
        ModelAuthorizationGrantCreateRequest.model_validate(
            {
                "providerId": provider.provider_id,
                "purpose": "capability_probe",
                "maxCalls": 2,
                "maxOutputTokens": 32,
            }
        )
    )

    fake = NormalizedCompletion(
        text="pong",
        finish_reason="stop",
        usage=NormalizedUsage(
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            raw={"prompt_tokens": 1, "completion_tokens": 1},
            source="provider_reported",
        ),
        provider_request_id="req_1",
        tool_calls=[{"type": "function", "function": {"name": "noop"}}],
    )

    with patch("agentforge.models.gateway.get_adapter") as get_adapter:
        adapter = get_adapter.return_value
        adapter.complete.return_value = fake
        call = gw.invoke(
            ModelCallCreateRequest.model_validate(
                {
                    "providerId": provider.provider_id,
                    "grantId": grant.grant_id,
                    "purpose": "capability_probe",
                    "messages": [{"role": "user", "content": "ping"}],
                    "idempotencyKey": "idem_1",
                }
            )
        )

    assert call.status == "succeeded"
    assert call.result_text == "pong"
    assert any(isinstance(r, UsageRecordRow) for r in session.added)
    # Tool calls must not be executed — only present on adapter result.
    adapter.complete.assert_called_once()
