"""Local credentials: client token file + server-side verification registry."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import secrets
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentforge.core.config import Settings


class CredentialError(RuntimeError):
    """Safe, non-secret diagnostic for local credential setup."""


@dataclass(frozen=True)
class CredentialRecord:
    credential_id: str
    digest_hex: str
    version: int
    scope: str
    created_at: str
    expires_at: str | None
    revoked_at: str | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        current = now or datetime.now(UTC)
        return datetime.fromisoformat(self.expires_at) <= current


def _restrict_directory(directory: Path) -> None:
    if directory.is_symlink() or directory.is_junction():
        raise CredentialError("Credential directory must not be a link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, check=True, timeout=10
        )
        row = next(csv.reader(result.stdout.decode(errors="replace").strip().splitlines()))
        sid = row[-1]
        if not sid.startswith("S-1-"):
            raise CredentialError("Cannot determine current user identity")
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    else:
        directory.chmod(0o700)


def _assert_safe_path(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
        raise CredentialError("Credential path must not be a link")


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def token_digest_hex(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "credentials": [], "activeCredentialId": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError("Credential registry is unreadable or corrupted") from exc
    if not isinstance(data, dict) or "credentials" not in data:
        raise CredentialError("Credential registry is unreadable or corrupted")
    return data


def _save_registry(path: Path, data: dict[str, Any]) -> None:
    _restrict_directory(path.parent)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        tmp.chmod(0o600)
    tmp.replace(path)


def _write_token_file(path: Path, token: str) -> None:
    _restrict_directory(path.parent)
    if path.exists():
        _assert_safe_path(path)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(token + "\n")


def _read_token_file(path: Path) -> str:
    _assert_safe_path(path)
    try:
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise CredentialError("Credential file permissions must be 0600")
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CredentialError("Run 'agentforge init' to initialize local credentials") from exc
    if (
        len(token) < 32
        or len(token) > 512
        or not token.isascii()
        or any(c.isspace() for c in token)
    ):
        raise CredentialError("Invalid local credential; configure a valid API token")
    return token


def _ensure_registry_for_existing_token(settings: Settings, token: str) -> None:
    """Migrate pre-BE-03 token files into a verification registry."""
    registry_path = settings.credential_registry_path
    if registry_path.exists():
        return
    credential_id = f"cred_{uuid.uuid4().hex}"
    record = {
        "credentialId": credential_id,
        "digestHex": token_digest_hex(token),
        "version": 1,
        "scope": "local-api",
        "createdAt": _iso(_utc_now()),
        "expiresAt": None,
        "revokedAt": None,
    }
    _save_registry(
        registry_path,
        {"version": 1, "activeCredentialId": credential_id, "credentials": [record]},
    )


def initialize_token(settings: Settings, *, expires_days: int | None = 365) -> bool:
    """Create once; never silently overwrite an existing token."""
    if settings.api_token is not None:
        return False
    path = settings.token_path
    registry_path = settings.credential_registry_path
    if path.exists():
        token = _read_token_file(path)
        _ensure_registry_for_existing_token(settings, token)
        return False
    if registry_path.exists():
        read_token(settings)
        return False
    token = secrets.token_urlsafe(48)
    credential_id = f"cred_{uuid.uuid4().hex}"
    expires_at = _iso(_utc_now() + timedelta(days=expires_days)) if expires_days else None
    record = {
        "credentialId": credential_id,
        "digestHex": token_digest_hex(token),
        "version": 1,
        "scope": "local-api",
        "createdAt": _iso(_utc_now()),
        "expiresAt": expires_at,
        "revokedAt": None,
    }
    try:
        _write_token_file(path, token)
        _save_registry(
            registry_path,
            {"version": 1, "activeCredentialId": credential_id, "credentials": [record]},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CredentialError("Cannot create a private credential file") from exc
    return True


def read_token(settings: Settings) -> str:
    if settings.api_token is not None:
        return settings.api_token.get_secret_value()
    token = _read_token_file(settings.token_path)
    _ensure_registry_for_existing_token(settings, token)
    return token


def list_credential_status(settings: Settings) -> dict[str, Any]:
    """Public status without token material."""
    if settings.api_token is not None:
        return {
            "source": "environment",
            "activeCredentialId": "env",
            "credentials": [
                {
                    "credentialId": "env",
                    "version": 1,
                    "scope": "local-api",
                    "expiresAt": None,
                    "revoked": False,
                    "active": True,
                }
            ],
        }
    data = _load_registry(settings.credential_registry_path)
    active = data.get("activeCredentialId")
    items = []
    for raw in data.get("credentials", []):
        items.append(
            {
                "credentialId": raw["credentialId"],
                "version": raw["version"],
                "scope": raw.get("scope", "local-api"),
                "createdAt": raw.get("createdAt"),
                "expiresAt": raw.get("expiresAt"),
                "revoked": raw.get("revokedAt") is not None,
                "active": raw["credentialId"] == active and raw.get("revokedAt") is None,
            }
        )
    return {"source": "registry", "activeCredentialId": active, "credentials": items}


def rotate_token(settings: Settings, *, expires_days: int | None = 365) -> dict[str, Any]:
    """Prepare new record → switch client token → revoke previous active."""
    if settings.api_token is not None:
        raise CredentialError("Cannot rotate when AGENTFORGE_API_TOKEN is set in the environment")
    data = _load_registry(settings.credential_registry_path)
    old_id = data.get("activeCredentialId")
    new_token = secrets.token_urlsafe(48)
    credential_id = f"cred_{uuid.uuid4().hex}"
    version = max((int(c.get("version", 0)) for c in data.get("credentials", [])), default=0) + 1
    expires_at = _iso(_utc_now() + timedelta(days=expires_days)) if expires_days else None
    record = {
        "credentialId": credential_id,
        "digestHex": token_digest_hex(new_token),
        "version": version,
        "scope": "local-api",
        "createdAt": _iso(_utc_now()),
        "expiresAt": expires_at,
        "revokedAt": None,
    }
    credentials = list(data.get("credentials", []))
    credentials.append(record)
    data["credentials"] = credentials
    data["activeCredentialId"] = credential_id
    _save_registry(settings.credential_registry_path, data)
    _write_token_file(settings.token_path, new_token)
    if old_id:
        revoke_credential(settings, old_id, allow_missing_active=True)
    return {"credentialId": credential_id, "version": version, "revokedCredentialId": old_id}


def revoke_credential(
    settings: Settings, credential_id: str, *, allow_missing_active: bool = False
) -> None:
    if settings.api_token is not None:
        raise CredentialError("Cannot revoke when AGENTFORGE_API_TOKEN is set in the environment")
    data = _load_registry(settings.credential_registry_path)
    found = False
    for raw in data.get("credentials", []):
        if raw["credentialId"] == credential_id:
            raw["revokedAt"] = _iso(_utc_now())
            found = True
            break
    if not found:
        raise CredentialError("Credential id not found")
    if data.get("activeCredentialId") == credential_id:
        data["activeCredentialId"] = None
        if not allow_missing_active:
            # Keep registry consistent; caller may rotate next.
            pass
    _save_registry(settings.credential_registry_path, data)


class CredentialVerifier:
    """Re-reads registry so rotate/revoke take effect without API restart."""

    def __init__(self, settings: Settings, *, reload_interval_seconds: float = 0.5) -> None:
        self._settings = settings
        self._reload_interval = reload_interval_seconds
        self._mtime: float | None = None
        self._cache: dict[str, CredentialRecord] = {}
        self._refresh(force=True)

    def _refresh(self, *, force: bool = False) -> None:
        if self._settings.api_token is not None:
            digest = token_digest_hex(self._settings.api_token.get_secret_value())
            self._cache = {
                digest: CredentialRecord(
                    credential_id="env",
                    digest_hex=digest,
                    version=1,
                    scope="local-api",
                    created_at=_iso(_utc_now()),
                    expires_at=None,
                    revoked_at=None,
                )
            }
            return
        path = self._settings.credential_registry_path
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            raise CredentialError("Credential registry is unreadable or corrupted") from exc
        now = time.monotonic()
        if (
            not force
            and self._mtime == mtime
            and getattr(self, "_checked_at", 0) + self._reload_interval > now
        ):
            return
        data = _load_registry(path)
        cache: dict[str, CredentialRecord] = {}
        for raw in data.get("credentials", []):
            record = CredentialRecord(
                credential_id=raw["credentialId"],
                digest_hex=raw["digestHex"],
                version=int(raw["version"]),
                scope=raw.get("scope", "local-api"),
                created_at=raw["createdAt"],
                expires_at=raw.get("expiresAt"),
                revoked_at=raw.get("revokedAt"),
            )
            cache[record.digest_hex] = record
        self._cache = cache
        self._mtime = mtime
        self._checked_at = now

    def verify(self, token: str) -> CredentialRecord:
        self._refresh()
        digest = token_digest_hex(token)
        record = self._cache.get(digest)
        if record is None or record.revoked or record.is_expired():
            raise CredentialError("Authentication required")
        return record
