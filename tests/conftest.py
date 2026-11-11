import pytest

from agentforge.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    """Do not read developer credentials or .env during tests."""
    import os

    for key in list(os.environ):
        if key.startswith("AGENTFORGE_") and key != "AGENTFORGE_TEST_DATABASE_URL":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, api_token="test-token-" + "x" * 48, state_dir=tmp_path / "state"
    )
