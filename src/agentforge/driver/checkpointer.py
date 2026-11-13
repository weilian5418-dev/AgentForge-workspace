"""Fenced PostgresSaver — lease checks share the checkpoint connection transaction."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row

from agentforge.contracts.collaboration import LeaseFence
from agentforge.driver.fencing import DriverFenceLost


class FencedPostgresSaver:
    """Compose PostgresSaver; put/put_writes require a live LeaseFence on ``self.conn``."""

    def __init__(
        self,
        conn: Connection[dict[str, Any]],
        fence: LeaseFence,
        *,
        delegate: PostgresSaver | None = None,
        serde: Any | None = None,
    ) -> None:
        self.conn = conn
        self.fence = fence
        self._lock = threading.RLock()
        self.delegate = delegate or PostgresSaver(conn, serde=serde)

    @property
    def serde(self) -> Any:
        return self.delegate.serde

    @property
    def config_specs(self) -> Any:
        return self.delegate.config_specs

    def get_next_version(self, current: str | None, channel: None) -> str:
        return self.delegate.get_next_version(current, channel)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        with self._lock:
            return self.delegate.get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        # Materialize under the connection lock — do not return a live cursor.
        with self._lock:
            return iter(list(self.delegate.list(config, filter=filter, before=before, limit=limit)))

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        with self._lock:
            with self.conn.transaction():
                self._assert_fence_on_connection()
                result = self.delegate.put(config, checkpoint, metadata, new_versions)
                self._assert_fence_before_commit()
                return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with self._lock:
            with self.conn.transaction():
                self._assert_fence_on_connection()
                self.delegate.put_writes(config, writes, task_id, task_path)
                self._assert_fence_before_commit()

    def _assert_fence_on_connection(self) -> None:
        self._assert_fence(stage="pre")

    def _assert_fence_before_commit(self) -> None:
        self._assert_fence(stage="commit")

    def _assert_fence(self, *, stage: str) -> None:
        fence = self.fence
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, status_version, active_generation FROM app.runs "
                "WHERE id = %s FOR UPDATE",
                (fence.run_id,),
            )
            run = cur.fetchone()
            if run is None:
                raise DriverFenceLost(f"[{stage}] run {fence.run_id} missing")

            cur.execute(
                "SELECT owner, token, generation, expires_at FROM app.run_leases "
                "WHERE run_id = %s FOR UPDATE",
                (fence.run_id,),
            )
            lease = cur.fetchone()
            cur.execute("SELECT clock_timestamp() AS now")
            now_row = cur.fetchone()
            now = now_row["now"] if now_row else None
            if (
                lease is None
                or lease["owner"] != fence.owner
                or lease["token"] != fence.token
                or int(lease["generation"]) != int(fence.lease_generation)
                or lease["expires_at"] <= now
            ):
                raise DriverFenceLost(
                    f"[{stage}] run lease {fence.run_id} is not valid for checkpoint write"
                )

            if fence.thread_id is not None:
                cur.execute(
                    "SELECT status, binding_hash, execution_generation "
                    "FROM app.driver_threads WHERE thread_id = %s FOR UPDATE",
                    (fence.thread_id,),
                )
                thread = cur.fetchone()
                if thread is None or thread["status"] != "active":
                    raise DriverFenceLost(f"[{stage}] driver thread {fence.thread_id} not active")
                if fence.binding_hash and thread["binding_hash"] != fence.binding_hash:
                    raise DriverFenceLost(f"[{stage}] binding_hash mismatch")
                if int(thread["execution_generation"]) != int(run["active_generation"]):
                    raise DriverFenceLost(f"[{stage}] execution generation mismatch")


def open_graph_connection(
    dsn: str, *, search_path: str = "graph, app, public"
) -> Connection[dict[str, Any]]:
    """Dedicated AUTOCOMMIT psycopg connection for graph advisory lock + checkpoint."""
    conn = Connection.connect(dsn, autocommit=True, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {search_path}")
    return conn


def try_acquire_graph_advisory_lock(conn: Connection[dict[str, Any]], run_id: str) -> bool:
    """Session-level try-lock for serializing drive_once on one Run."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))",
            (f"driver-graph:{run_id}",),
        )
        row = cur.fetchone()
        if row is None:
            return False
        value = row[0] if not isinstance(row, dict) else next(iter(row.values()))
        return bool(value)


def release_graph_advisory_lock(conn: Connection[dict[str, Any]], run_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_unlock(hashtext(%s))",
            (f"driver-graph:{run_id}",),
        )


def setup_graph_schema(conn: Connection[dict[str, Any]]) -> None:
    """Install/upgrade PostgresSaver tables once under an install advisory lock.

    Resident Drivers must not call this on every boot without the migrator role.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext('agentforge-graph-setup'))")
    try:
        PostgresSaver(conn).setup()
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext('agentforge-graph-setup'))")
