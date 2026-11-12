"""Endpoint network policy checks for model providers (D01 / F07)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from agentforge.contracts.providers import NetworkPolicy

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_REDIRECTS_DEFAULT = 3


class NetworkPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _host_ips(host: str) -> list[str]:
    """Resolve host to IP strings; failure is a hard deny for remote/private checks."""
    if host in _LOCAL_HOSTS:
        return ["127.0.0.1"] if host != "::1" else ["::1"]
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkPolicyError("dns_resolution_failed", f"Cannot resolve host {host}") from exc
    ips: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in ips:
            ips.append(addr)
    if not ips:
        raise NetworkPolicyError("dns_resolution_failed", f"No addresses for host {host}")
    return ips


def classify_ip(ip_text: str) -> str:
    addr = ipaddress.ip_address(ip_text)
    if addr.is_loopback:
        return "local"
    if (
        addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return "private"
    # IPv4-mapped etc. already handled by ip_address
    return "remote"


def classify_endpoint(base_url: str, *, resolve_dns: bool = True) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        raise NetworkPolicyError("invalid_host", "URL must include a hostname")
    if host in _LOCAL_HOSTS or host.endswith(".localhost"):
        return "local"
    # Hostname heuristics alone are insufficient — prefer resolved addresses.
    if not resolve_dns:
        if host.endswith(".local") or host.startswith("10.") or host.startswith("192.168."):
            return "private"
        return "remote"
    classes = {classify_ip(ip) for ip in _host_ips(host)}
    if "local" in classes:
        return "local"
    if "private" in classes:
        return "private"
    return "remote"


def validate_endpoint(base_url: str, policy: NetworkPolicy, *, resolve_dns: bool = True) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise NetworkPolicyError("invalid_scheme", "Only http(s) endpoints are allowed")
    if parsed.username or parsed.password:
        raise NetworkPolicyError("credentials_in_url", "URL userinfo is not allowed")
    actual = classify_endpoint(base_url, resolve_dns=resolve_dns)
    if policy.network_class == "local" and actual != "local":
        raise NetworkPolicyError(
            "network_class_mismatch",
            "Local network policy cannot authorize non-local endpoints",
        )
    if policy.network_class == "private" and actual == "remote":
        raise NetworkPolicyError(
            "network_class_mismatch",
            "Private network policy cannot authorize remote endpoints",
        )
    if policy.network_class == "remote":
        if actual != "remote":
            raise NetworkPolicyError(
                "network_class_mismatch",
                "Remote network policy cannot authorize local/private endpoints",
            )
        if parsed.scheme != "https":
            raise NetworkPolicyError("https_required", "Remote endpoints require HTTPS")
    if policy.proxy_url:
        # Proxy is an independent hop — must itself satisfy the same class rules.
        validate_endpoint(policy.proxy_url, policy, resolve_dns=resolve_dns)


def max_redirects(policy: NetworkPolicy) -> int:
    if not policy.allow_redirects:
        return 0
    return _MAX_REDIRECTS_DEFAULT
