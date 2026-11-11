from agentforge.storage.base import Base
from agentforge.storage.models import APP_BASELINE_TABLES, APP_V14_TABLES


def test_baseline_models_cover_existing_app_tables() -> None:
    table_names = {table.name for table in Base.metadata.tables.values()}
    assert table_names == set(APP_BASELINE_TABLES) | set(APP_V14_TABLES)
    assert all(table.schema == "app" for table in Base.metadata.tables.values())
