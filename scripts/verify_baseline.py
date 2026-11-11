"""Compare live PostgreSQL app schema to SQLAlchemy metadata. Exit 1 on drift."""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect

from agentforge.core.config import Settings
from agentforge.storage import models as _models  # noqa: F401
from agentforge.storage.base import Base


def _col_key(name: str, nullable: bool, type_name: str) -> str:
    return f"{name}|null={nullable}|type={type_name}"


def main() -> int:
    url = Settings().database_url
    if url is None:
        print("Set AGENTFORGE_DATABASE_URL (prefer migrator for inspection)", file=sys.stderr)
        return 2

    engine = create_engine(url.get_secret_value(), hide_parameters=True)
    try:
        inspector = inspect(engine)
        errors: list[str] = []
        expected_tables = {t.name for t in Base.metadata.tables.values() if t.schema == "app"}
        live_tables = set(inspector.get_table_names(schema="app")) - {"alembic_version"}
        missing = expected_tables - live_tables
        extra = live_tables - expected_tables
        if missing:
            errors.append(f"missing tables: {sorted(missing)}")
        if extra:
            errors.append(f"unexpected tables: {sorted(extra)}")

        for table in Base.metadata.sorted_tables:
            if table.schema != "app" or table.name not in live_tables:
                continue
            live_cols = {c["name"]: c for c in inspector.get_columns(table.name, schema="app")}
            for column in table.columns:
                live = live_cols.get(column.name)
                if live is None:
                    errors.append(f"{table.name}.{column.name}: missing in database")
                    continue
                live_type = str(live["type"]).lower()
                model_type = str(column.type).lower()
                # Normalize common PG aliases.
                matched = False
                if ("datetime" in model_type or "timestamp" in model_type) and (
                    "timestamp" in live_type or "datetime" in live_type
                ):
                    matched = True
                aliases = (
                    ({"bigint", "int8"}, {"bigint", "int8"}),
                    ({"integer", "int", "int4"}, {"integer", "int", "int4"}),
                    ({"jsonb", "json"}, {"jsonb", "json"}),
                    ({"boolean", "bool"}, {"boolean", "bool"}),
                    ({"text"}, {"text"}),
                )
                for model_names, live_names in aliases:
                    if any(n in model_type for n in model_names) and any(
                        n in live_type for n in live_names
                    ):
                        matched = True
                        break
                if not matched:
                    errors.append(
                        f"{table.name}.{column.name}: type model={column.type} live={live['type']}"
                    )
                if bool(live["nullable"]) != bool(column.nullable):
                    errors.append(
                        f"{table.name}.{column.name}: nullability model={column.nullable} "
                        f"live={live['nullable']}"
                    )

            live_pk = set(
                inspector.get_pk_constraint(table.name, schema="app").get("constrained_columns")
                or []
            )
            model_pk = {c.name for c in table.primary_key.columns}
            if live_pk != model_pk:
                errors.append(f"{table.name}: pk model={sorted(model_pk)} live={sorted(live_pk)}")

        if errors:
            print("Baseline drift detected:")
            for item in errors:
                print(f" - {item}")
            return 1
        print("Baseline OK: live app schema matches SQLAlchemy models for checked attributes.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
