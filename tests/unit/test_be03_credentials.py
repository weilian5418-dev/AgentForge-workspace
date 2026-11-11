import time

import pytest

from agentforge.core.config import Settings
from agentforge.core.credentials import (
    CredentialError,
    CredentialVerifier,
    initialize_token,
    list_credential_status,
    read_token,
    revoke_credential,
    rotate_token,
)


def test_rotate_revokes_old_and_verifier_reloads(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    assert initialize_token(settings)
    old = read_token(settings)
    verifier = CredentialVerifier(settings, reload_interval_seconds=0)
    assert verifier.verify(old).credential_id

    result = rotate_token(settings)
    new = read_token(settings)
    assert new != old
    assert result["credentialId"]
    time.sleep(0.05)
    with pytest.raises(CredentialError):
        verifier.verify(old)
    assert verifier.verify(new).credential_id == result["credentialId"]

    status = list_credential_status(settings)
    assert status["activeCredentialId"] == result["credentialId"]
    revoked = [
        c for c in status["credentials"] if c["credentialId"] == result["revokedCredentialId"]
    ]
    assert revoked and revoked[0]["revoked"] is True


def test_revoke_without_replacement(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    initialize_token(settings)
    status = list_credential_status(settings)
    cred_id = status["activeCredentialId"]
    assert cred_id
    revoke_credential(settings, cred_id)
    token = read_token(settings)
    verifier = CredentialVerifier(settings, reload_interval_seconds=0)
    with pytest.raises(CredentialError):
        verifier.verify(token)
