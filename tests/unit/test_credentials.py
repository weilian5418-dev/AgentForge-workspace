import os

import pytest

from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError, initialize_token, read_token


def test_initialize_is_private_and_idempotent(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    assert initialize_token(settings)
    token = read_token(settings)
    assert len(token) >= 48
    assert not initialize_token(settings)
    assert read_token(settings) == token
    if os.name != "nt":
        assert settings.token_path.stat().st_mode & 0o077 == 0


def test_env_token_does_not_create_file(tmp_path):
    settings = Settings(_env_file=None, api_token="x" * 48, state_dir=tmp_path / "state")
    assert not initialize_token(settings)
    assert not settings.token_path.exists()


def test_missing_credential_is_explicit(tmp_path):
    with pytest.raises(CredentialError, match="agentforge init"):
        read_token(Settings(_env_file=None, state_dir=tmp_path / "absent"))
