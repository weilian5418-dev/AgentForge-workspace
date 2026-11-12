"""F07: redirects must re-validate; remote policy cannot reach link-local."""

from __future__ import annotations

import httpx
import pytest

from agentforge.contracts.providers import NetworkPolicy
from agentforge.models import adapters as adapters_mod
from agentforge.models.adapters import AdapterError, OpenAICompatibleAdapter
from agentforge.models.network import NetworkPolicyError, classify_ip, validate_endpoint


def test_classify_link_local_is_private() -> None:
    assert classify_ip("169.254.169.254") == "private"
    assert classify_ip("10.0.0.1") == "private"
    assert classify_ip("127.0.0.1") == "local"


def test_remote_policy_rejects_private_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agentforge.models.network._host_ips",
        lambda host: ["169.254.169.254"],
    )
    policy = NetworkPolicy.model_validate(
        {
            "networkClass": "remote",
            "allowRedirects": True,
            "connectTimeoutSeconds": 1,
            "totalTimeoutSeconds": 2,
        }
    )
    with pytest.raises(NetworkPolicyError) as exc:
        validate_endpoint("https://approved.example/v1", policy)
    assert exc.value.code == "network_class_mismatch"


def test_redirect_to_link_local_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = NetworkPolicy.model_validate(
        {
            "networkClass": "remote",
            "allowRedirects": True,
            "connectTimeoutSeconds": 1,
            "totalTimeoutSeconds": 2,
        }
    )

    def fake_ips(host: str) -> list[str]:
        host = host.lower()
        if host == "approved.example":
            return ["8.8.8.8"]
        return ["169.254.169.254"]

    monkeypatch.setattr("agentforge.models.network._host_ips", fake_ips)

    calls = {"n": 0}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            307,
            headers={"location": "http://169.254.169.254/review-synthetic"},
            request=request,
        )

    transport = httpx.MockTransport(handler)

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            self._client = real_client(transport=transport, follow_redirects=False, trust_env=False)

        def __enter__(self) -> httpx.Client:
            return self._client.__enter__()

        def __exit__(self, *args: object) -> None:
            self._client.__exit__(*args)

    monkeypatch.setattr(adapters_mod.httpx, "Client", _Client)

    with pytest.raises(AdapterError) as exc:
        OpenAICompatibleAdapter().complete(
            base_url="https://approved.example/v1",
            model_id="m",
            messages=[{"role": "user", "content": "x"}],
            max_output_tokens=8,
            auth_header={"Authorization": "Bearer t"},
            policy=policy,
        )
    assert exc.value.code in {
        "network_class_mismatch",
        "https_required",
        "invalid_scheme",
    }
    assert calls["n"] == 1
