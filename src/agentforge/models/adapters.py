"""Provider adapters: openai-compatible subset + Ollama native (D01).

Tool calls in model output are normalized but never executed here.
Redirect targets are re-validated against network policy (F07).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from agentforge.contracts.providers import NetworkPolicy
from agentforge.models.network import NetworkPolicyError, max_redirects, validate_endpoint


class AdapterError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(slots=True)
class NormalizedUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"


@dataclass(slots=True)
class NormalizedCompletion:
    text: str
    finish_reason: str | None
    usage: NormalizedUsage
    provider_request_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ProviderAdapter(Protocol):
    def complete(
        self,
        *,
        base_url: str,
        model_id: str,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        auth_header: dict[str, str] | None,
        policy: NetworkPolicy,
    ) -> NormalizedCompletion: ...


def _client(policy: NetworkPolicy) -> httpx.Client:
    # Never auto-follow redirects: each hop must re-pass validate_endpoint.
    kwargs: dict[str, object] = {
        "timeout": httpx.Timeout(
            policy.total_timeout_seconds,
            connect=policy.connect_timeout_seconds,
        ),
        "follow_redirects": False,
        "trust_env": False,
    }
    if policy.proxy_url:
        kwargs["proxy"] = policy.proxy_url
    return httpx.Client(**kwargs)  # type: ignore[arg-type]


def _request_with_redirect_guard(
    client: httpx.Client,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any],
    policy: NetworkPolicy,
) -> httpx.Response:
    current = url
    auth_headers = dict(headers)
    hops = 0
    limit = max_redirects(policy)
    while True:
        try:
            validate_endpoint(current, policy)
        except NetworkPolicyError as exc:
            raise AdapterError(exc.code, exc.message) from exc
        response = client.request(method, current, headers=auth_headers, json=json_body)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        if hops >= limit:
            raise AdapterError(
                "redirect_not_allowed",
                "Redirect refused: allowRedirects is false or hop limit exceeded",
            )
        location = response.headers.get("location")
        if not location:
            raise AdapterError("redirect_missing_location", "Redirect without Location header")
        next_url = urljoin(current, location)
        # Do not forward Authorization across host changes.
        prev_host = (urlparse(current).hostname or "").lower()
        next_host = (urlparse(next_url).hostname or "").lower()
        if prev_host != next_host:
            auth_headers.pop("Authorization", None)
        current = next_url
        hops += 1
        if response.status_code in {301, 302, 303}:
            method = "GET"
            json_body = {}


class OpenAICompatibleAdapter:
    def complete(
        self,
        *,
        base_url: str,
        model_id: str,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        auth_header: dict[str, str] | None,
        policy: NetworkPolicy,
    ) -> NormalizedCompletion:
        try:
            validate_endpoint(base_url, policy)
        except NetworkPolicyError as exc:
            raise AdapterError(exc.code, exc.message) from exc
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers.update(auth_header)
        body = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        try:
            with _client(policy) as client:
                response = _request_with_redirect_guard(
                    client,
                    method="POST",
                    url=url,
                    headers=headers,
                    json_body=body,
                    policy=policy,
                )
        except AdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise AdapterError(
                "PROVIDER_UNAVAILABLE", "Provider timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                "PROVIDER_UNAVAILABLE", f"Provider HTTP error: {exc}", retryable=True
            ) from exc

        if response.status_code in {401, 403}:
            raise AdapterError("PROVIDER_AUTH_FAILED", "Upstream provider authentication failed")
        if response.status_code >= 500:
            raise AdapterError(
                "PROVIDER_UNAVAILABLE", f"Provider status {response.status_code}", retryable=True
            )
        if response.status_code >= 400:
            raise AdapterError(
                "INVALID_REQUEST", f"Provider rejected request ({response.status_code})"
            )

        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = str(message.get("content") or "")
        tool_calls = list(message.get("tool_calls") or [])
        usage_raw = data.get("usage") or {}
        usage = NormalizedUsage(
            input_tokens=_int_or_none(usage_raw.get("prompt_tokens")),
            output_tokens=_int_or_none(usage_raw.get("completion_tokens")),
            total_tokens=_int_or_none(usage_raw.get("total_tokens")),
            raw=dict(usage_raw) if isinstance(usage_raw, dict) else {},
            source="provider_reported" if usage_raw else "unknown",
        )
        return NormalizedCompletion(
            text=text,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            provider_request_id=_as_str(data.get("id")),
            tool_calls=tool_calls,
        )


class OllamaAdapter:
    def complete(
        self,
        *,
        base_url: str,
        model_id: str,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        auth_header: dict[str, str] | None,
        policy: NetworkPolicy,
    ) -> NormalizedCompletion:
        try:
            validate_endpoint(base_url, policy)
        except NetworkPolicyError as exc:
            raise AdapterError(exc.code, exc.message) from exc
        url = base_url.rstrip("/") + "/api/chat"
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers.update(auth_header)
        body = {
            "model": model_id,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_output_tokens},
        }
        try:
            with _client(policy) as client:
                response = _request_with_redirect_guard(
                    client,
                    method="POST",
                    url=url,
                    headers=headers,
                    json_body=body,
                    policy=policy,
                )
        except AdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise AdapterError("PROVIDER_UNAVAILABLE", "Ollama timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                "PROVIDER_UNAVAILABLE", f"Ollama HTTP error: {exc}", retryable=True
            ) from exc

        if response.status_code in {401, 403}:
            raise AdapterError("PROVIDER_AUTH_FAILED", "Ollama authentication failed")
        if response.status_code >= 400:
            raise AdapterError(
                "PROVIDER_UNAVAILABLE", f"Ollama status {response.status_code}", retryable=True
            )

        data = response.json()
        message = data.get("message") or {}
        text = str(message.get("content") or "")
        prompt_eval = _int_or_none(data.get("prompt_eval_count"))
        eval_count = _int_or_none(data.get("eval_count"))
        total = None
        if prompt_eval is not None or eval_count is not None:
            total = (prompt_eval or 0) + (eval_count or 0)
        usage = NormalizedUsage(
            input_tokens=prompt_eval,
            output_tokens=eval_count,
            total_tokens=total,
            raw={
                k: data.get(k)
                for k in ("prompt_eval_count", "eval_count", "done_reason")
                if k in data
            },
            source="provider_reported" if total is not None else "unknown",
        )
        return NormalizedCompletion(
            text=text,
            finish_reason=_as_str(data.get("done_reason")),
            usage=usage,
            provider_request_id=None,
            tool_calls=[],
        )


def get_adapter(adapter_type: str) -> OpenAICompatibleAdapter | OllamaAdapter:
    if adapter_type == "openai_compatible":
        return OpenAICompatibleAdapter()
    if adapter_type == "ollama":
        return OllamaAdapter()
    raise AdapterError("unsupported_adapter", f"Unknown adapterType: {adapter_type}")


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value) if isinstance(value, str | int | float) else None
    except (TypeError, ValueError):
        return None


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
