"""D02: queue URL must be explicit — API DB URL is not enough."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from agentforge.core.config import Settings


def test_require_queue_url_does_not_silently_use_api_url(tmp_path) -> None:
    settings = Settings(
        state_dir=tmp_path / "st",
        database_url=SecretStr(
            "postgresql+psycopg://agentforge_api:x@127.0.0.1:5432/agentforge"
        ),
        queue_database_url=None,
    )
    with pytest.raises(ValueError, match="QUEUE_DATABASE_URL"):
        settings.require_queue_database_url()


def test_queue_url_accepted(tmp_path) -> None:
    settings = Settings(
        state_dir=tmp_path / "st",
        queue_database_url=SecretStr(
            "postgresql+psycopg://agentforge_driver:x@127.0.0.1:5432/agentforge"
        ),
    )
    assert "agentforge_driver" in settings.require_queue_database_url().get_secret_value()
