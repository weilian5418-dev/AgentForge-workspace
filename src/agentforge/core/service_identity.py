"""Short-lived HMAC tickets issued only by trusted local services, never public API."""

import base64
import hashlib
import hmac
import json
import time

from agentforge.core.config import Settings


def issue_ticket(
    settings: Settings, *, subject: str, audience: str, request_id: str, ttl: int = 60
) -> str:
    if (
        settings.internal_signing_key is None
        or len(settings.internal_signing_key.get_secret_value()) < 32
    ):
        raise ValueError("Internal signing key is not configured")
    payload = {
        "sub": subject,
        "aud": audience,
        "request": request_id,
        "exp": int(time.time()) + min(ttl, 300),
    }
    raw = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(
        settings.internal_signing_key.get_secret_value().encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    return raw + "." + sig


def verify_ticket(settings: Settings, token: str, *, audience: str, request_id: str | None) -> str:
    if settings.internal_signing_key is None:
        raise ValueError("Internal signing key is not configured")
    if len(token) > 4096:
        raise ValueError("Internal ticket too large")
    raw, sig = token.split(".", 1)
    expected = hmac.new(
        settings.internal_signing_key.get_secret_value().encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid internal ticket")
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    if (
        payload.get("aud") != audience
        or (request_id is not None and payload.get("request") != request_id)
        or payload.get("exp", 0) <= time.time()
        or not payload.get("sub")
    ):
        raise ValueError("Internal ticket scope or expiry mismatch")
    return str(payload["sub"])
