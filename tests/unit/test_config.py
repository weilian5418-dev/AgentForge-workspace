import pytest
from pydantic import ValidationError

from agentforge.core.config import Settings


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "::"])
def test_bind_is_loopback_only(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, host=host)


def test_settings_hide_secrets():
    settings = Settings(
        _env_file=None,
        api_token="s" * 48,
        database_url="postgresql+psycopg://user:private-password@localhost/agentforge",
    )
    assert "private-password" not in repr(settings)
    assert "s" * 48 not in repr(settings)


@pytest.mark.parametrize(
    "value", ["sqlite:///data.db", "postgresql://user@localhost/db", "garbage"]
)
def test_database_driver_is_explicit(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=value)


def test_ipv6_base_url():
    assert Settings(_env_file=None, host="::1").base_url == "http://[::1]:8765"
