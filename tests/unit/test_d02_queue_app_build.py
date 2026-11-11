"""F08: Procrastinate app must build without TypeError."""

from __future__ import annotations

from unittest.mock import patch

from pydantic import SecretStr

from agentforge.core.config import Settings
from agentforge.queue.app import build_queue_app


def test_build_queue_app_registers_tasks() -> None:
    settings = Settings.model_construct(
        queue_database_url=SecretStr(
            "postgresql+psycopg://u:p@127.0.0.1:5432/agentforge_queue"
        )
    )
    # Do not open a DB connection — only construct App + register blueprint.
    with patch("agentforge.queue.app.SyncPsycopgConnector"):
        app = build_queue_app(settings)  # type: ignore[arg-type]
    assert "agentforge.execute_model_operation" in app.tasks
