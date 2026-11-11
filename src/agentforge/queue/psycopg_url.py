"""Convert SQLAlchemy URLs to libpq/psycopg connection strings for Procrastinate."""

from __future__ import annotations


def to_psycopg_conninfo(database_url: str) -> str:
    url = database_url.strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url
