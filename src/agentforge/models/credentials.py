"""Model credential store — separate from platform API tokens; never log secrets."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError, _assert_safe_path, _restrict_directory


class ModelCredentialStore:
    def __init__(self, settings: Settings) -> None:
        self.root = settings.state_dir.resolve() / "credentials" / "model"
        _restrict_directory(self.root)
        self.registry_path = self.root / "registry.json"

    def _load(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"credentials": {}}
        _assert_safe_path(self.registry_path)
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        _assert_safe_path(self.registry_path.parent)
        tmp = self.registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if os.name != "nt":
            tmp.chmod(0o600)
        tmp.replace(self.registry_path)

    def register(self, secret: str, *, label: str | None = None) -> dict[str, str]:
        if not secret or len(secret) > 4096:
            raise CredentialError("Invalid model credential length")
        ref = f"mcred_{uuid.uuid4().hex[:20]}"
        secret_path = self.root / f"{ref}.secret"
        _assert_safe_path(self.root)
        secret_path.write_text(secret, encoding="utf-8")
        if os.name != "nt":
            secret_path.chmod(0o600)
        data = self._load()
        data["credentials"][ref] = {
            "credentialRef": ref,
            "label": label,
            "status": "configured",
            "createdAt": datetime.now(UTC).isoformat(),
            "revokedAt": None,
            "secretFile": secret_path.name,
        }
        self._save(data)
        return {"credentialRef": ref, "status": "configured"}

    def resolve(self, credential_ref: str) -> str:
        data = self._load()
        row = data.get("credentials", {}).get(credential_ref)
        if row is None:
            raise CredentialError("Model credential not found")
        if row.get("status") != "configured":
            raise CredentialError("Model credential revoked")
        path = self.root / str(row["secretFile"])
        _assert_safe_path(path)
        return path.read_text(encoding="utf-8")

    def status(self, credential_ref: str | None) -> str:
        if credential_ref is None:
            return "not_required"
        data = self._load()
        row = data.get("credentials", {}).get(credential_ref)
        if row is None:
            return "missing"
        return str(row.get("status") or "missing")

    def revoke(self, credential_ref: str) -> None:
        data = self._load()
        row = data.get("credentials", {}).get(credential_ref)
        if row is None:
            raise CredentialError("Model credential not found")
        row["status"] = "revoked"
        row["revokedAt"] = datetime.now(UTC).isoformat()
        secret_path = self.root / str(row["secretFile"])
        if secret_path.exists():
            # Overwrite then delete — do not leave plaintext.
            secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
            secret_path.unlink(missing_ok=True)
        self._save(data)
