"""Serialize native traffic per instance without holding a business transaction.

A stop cannot overtake an already claimed send and then freeze before that send starts.
"""

import hashlib
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text

from agentforge.instances.errors import InstanceError


@contextmanager
def native_lane(engine: Any, instance_id: str) -> Any:
    key = int.from_bytes(
        hashlib.sha256(("native-lane:" + instance_id).encode()).digest()[:8], "big", signed=True
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar()
        if not acquired:
            raise InstanceError(
                "native_lane_busy", "Another native operation must finish before this operation"
            )
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
