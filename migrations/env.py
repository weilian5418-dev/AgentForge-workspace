"""Explicit migrations only. Normal service startup never invokes this module."""

from alembic import context
from sqlalchemy import create_engine, pool

from agentforge.core.config import Settings
from agentforge.storage import models as _models  # noqa: F401
from agentforge.storage.base import Base

config = context.config
if getattr(config.cmd_opts, "autogenerate", False) and not Base.metadata.tables:
    raise RuntimeError("Define and reconcile application models before generating migrations")


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == "app"
    if type_ == "table":
        return parent_names.get("schema_qualified_table_name") in Base.metadata.tables
    return True


url = Settings().database_url
if url is None:
    raise RuntimeError("Set AGENTFORGE_DATABASE_URL for the authorized migration role")

# Existing database tables must be reconciled before any initial migration is authored.
# Queue/checkpoint schemas are managed by their own packages, never by this metadata.
if context.is_offline_mode():
    context.configure(
        url=url.get_secret_value(),
        target_metadata=Base.metadata,
        literal_binds=True,
        version_table_schema="app",
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url.get_secret_value(), poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table_schema="app",
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
