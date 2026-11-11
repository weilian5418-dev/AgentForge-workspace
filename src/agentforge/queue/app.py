"""Procrastinate application bound to the ``queue`` schema."""

from __future__ import annotations

from procrastinate import App, SyncPsycopgConnector

from agentforge.core.config import Settings
from agentforge.queue.constants import QUEUE_SCHEMA
from agentforge.queue.psycopg_url import to_psycopg_conninfo


def build_queue_app(settings: Settings) -> App:
    queue_url = settings.require_queue_database_url()
    conninfo = to_psycopg_conninfo(queue_url.get_secret_value())
    connector = SyncPsycopgConnector(
        conninfo=conninfo,
        kwargs={"options": f"-c search_path={QUEUE_SCHEMA},public"},
    )
    app = App(connector=connector)
    from agentforge.queue.tasks import blueprint

    # Procrastinate 3.x requires keyword-only namespace; empty keeps task names unchanged.
    app.add_tasks_from(blueprint, namespace="")
    return app


def open_app(settings: Settings) -> App:
    app = build_queue_app(settings)
    app.open()
    return app
