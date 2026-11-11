import os

import pytest

from agentforge.core.config import Settings
from agentforge.storage.database import check_database, create_database_engine


@pytest.mark.integration
def test_existing_postgres_read_only_connection():
    url = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AGENTFORGE_TEST_DATABASE_URL to run against an existing PostgreSQL")
    engine = create_database_engine(Settings(_env_file=None, database_url=url))
    try:
        assert check_database(engine).status == "available"
    finally:
        if engine is not None:
            engine.dispose()
