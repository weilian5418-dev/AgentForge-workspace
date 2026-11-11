"""Normalized request hashing for idempotency keys."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_for_hash(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [normalize_for_hash(item) for item in value]
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return str(value)


def request_content_hash(
    *,
    operation_type: str,
    target_refs: dict[str, Any],
    body: dict[str, Any],
) -> str:
    payload = {
        "operationType": operation_type,
        "targetRefs": normalize_for_hash(target_refs),
        "body": normalize_for_hash(body),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
