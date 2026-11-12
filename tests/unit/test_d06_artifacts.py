"""D06 artifact bus: fixed snapshot verify, publish≠release, GC, FS failures."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentforge.artifacts.service import ArtifactBusError, ArtifactService
from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.artifact_bus import (
    FreezeCandidateRequest,
    GcRequest,
    PublishRequest,
    VerifyCandidateRequest,
)
from agentforge.storage.models import (
    ArtifactCandidateRow,
    ArtifactPublicationRow,
    ContentObjectRow,
    RunnerOperationRow,
)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        key = getattr(row, "id", None)
        if key is None and hasattr(row, "content_hash"):
            key = row.content_hash
        if key is not None:
            self.store[(type(row), str(key))] = row

    def flush(self) -> None:
        return None

    def execute(self, statement: Any) -> None:
        assert "pg_advisory_xact_lock" in str(statement)

    def get(self, model: type, key: str, **_kwargs: Any) -> Any | None:
        return self.store.get((model, key))

    def delete(self, row: Any) -> None:
        key = getattr(row, "id", None) or getattr(row, "content_hash", None)
        if key is not None:
            self.store.pop((type(row), str(key)), None)
        if row in self.added:
            self.added.remove(row)

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        if "artifact_publications" in text:
            pubs = [r for r in self.added if isinstance(r, ArtifactPublicationRow)]
            return pubs[-1] if pubs else None
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt).lower()
        if "artifact_validation_reports" in text:
            from agentforge.storage.models import ArtifactValidationReportRow

            rows = [r for r in self.added if isinstance(r, ArtifactValidationReportRow)]
            return _Result(rows)
        if "artifact_publications" in text:
            return _Result([r for r in self.added if isinstance(r, ArtifactPublicationRow)])
        if "artifact_candidates" in text:
            return _Result([r for r in self.added if isinstance(r, ArtifactCandidateRow)])
        if "runner_operations" in text:
            return _Result([r for r in self.added if isinstance(r, RunnerOperationRow)])
        if "content_objects" in text:
            return _Result([r for r in self.added if isinstance(r, ContentObjectRow)])
        return _Result([])


def _seed_runner(session: _MemSession, *, operation_id: str = "rop_1", generation: int = 1) -> None:
    session.add(
        RunnerOperationRow(
            id=operation_id,
            profile_id="close_task",
            workspace_id="ws_1",
            workspace_root="/tmp",
            node_id="local",
            attempt_id="att_1",
            generation=generation,
            action_digest="a" * 64,
            scope="ws/",
            params_json={},
            status="succeeded",
            result_json={
                "executionReceipt": {
                    "operationId": operation_id,
                    "dispatchToken": "fixture",
                    "runnerSynchronousOnly": True,
                }
            },
            process_ref="host:rop_1",
            approval_id=None,
            require_effective_approval=False,
            message="ok",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )


def _workspace_with_evidence(tmp_path: Path, content: str = "hello") -> tuple[Path, str, str]:
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    evidence = {"stopped": True, "reason": "test"}
    import json

    evidence_bytes = json.dumps(evidence, sort_keys=True).encode("utf-8")
    evidence_path = ws / "_agentforge" / "stop_evidence.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(evidence_bytes)
    return ws, hashlib.sha256(evidence_bytes).hexdigest(), "src/a.py"


def _freeze(
    svc: ArtifactService,
    ws: Path,
    evidence_sha: str,
    rel: str,
    *,
    env_hash: str | None = None,
) -> Any:
    # Create a server-side receipt for the fixture's explicitly stopped operation.
    from agentforge.runner.service import RunnerService

    row = svc.session.get(RunnerOperationRow, "rop_1")
    proof = {
        "source": "runner",
        "operationId": "rop_1",
        "workspaceId": "ws_1",
        "attemptId": "att_1",
        "generation": 1,
        "evidenceSha256": evidence_sha,
        "writableScope": "ws/",
        "writesQuiesced": True,
        "checkedAt": datetime.now(UTC),
    }
    if row is not None:
        row.workspace_root = str(ws.resolve())
        ticket = RunnerService(svc.session).record_stop_evidence("rop_1")
        trusted = ticket.result["stopEvidence"]
        proof = {key: trusted[key] for key in proof}
        marker = ws / "_agentforge" / "stop_evidence.json"
        if evidence_sha != hashlib.sha256(marker.read_bytes()).hexdigest():
            proof["evidenceSha256"] = evidence_sha
    body = FreezeCandidateRequest.model_validate(
        {
            "projectId": "proj_1",
            "workspaceId": "ws_1",
            "workspaceRoot": str(ws),
            "relativePath": rel,
            "contractId": "ctr_1",
            "contractVersion": 1,
            "environmentHash": env_hash or ("b" * 64),
            "stopEvidence": proof,
            "kind": "code",
        }
    )
    return svc.freeze_candidate(body)


def test_store_atomic_and_verify(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    h = store.put_bytes(b"abc")
    assert store.has(h)
    assert store.get_bytes(h) == b"abc"
    assert store.verify(h)


def test_freeze_verify_publish_consume_denied(tmp_path: Path) -> None:
    session = _MemSession()
    _seed_runner(session)
    store = ArtifactStore(tmp_path / "store")
    svc = ArtifactService(session, store)  # type: ignore[arg-type]
    ws, evidence_sha, rel = _workspace_with_evidence(tmp_path)
    cand = _freeze(svc, ws, evidence_sha, rel)
    assert cand.state == "frozen"

    # Mutating workspace after freeze must not affect verification (fixed snapshot).
    (ws / rel).write_text("MUTATED", encoding="utf-8")

    report = svc.verify(
        VerifyCandidateRequest.model_validate(
            {"candidateId": cand.candidate_id, "validatorId": "sha256_presence"}
        )
    )
    assert report.status == "passed"
    report2 = svc.verify(
        VerifyCandidateRequest.model_validate(
            {"candidateId": cand.candidate_id, "validatorId": "utf8_text"}
        )
    )
    assert report2.status == "passed"

    pub = svc.publish(
        PublishRequest.model_validate(
            {
                "candidateId": cand.candidate_id,
                "idempotencyKey": "idem_1",
                "artifactId": "art_1",
            }
        )
    )
    assert pub.state == "published"
    assert pub.stage_released is False

    # Idempotent republish
    pub2 = svc.publish(
        PublishRequest.model_validate(
            {
                "candidateId": cand.candidate_id,
                "idempotencyKey": "idem_1",
                "artifactId": "art_1",
            }
        )
    )
    assert pub2.publication_id == pub.publication_id

    check = svc.can_consume("art_1", 1)
    assert check.allowed is False
    assert "d10" in check.reason.lower() or "stage" in check.reason.lower()


def test_reject_untrusted_stop_and_bad_evidence(tmp_path: Path) -> None:
    session = _MemSession()
    store = ArtifactStore(tmp_path / "store")
    svc = ArtifactService(session, store)  # type: ignore[arg-type]
    ws, evidence_sha, rel = _workspace_with_evidence(tmp_path)
    with pytest.raises(ArtifactBusError) as exc:
        _freeze(svc, ws, evidence_sha, rel)
    assert exc.value.code == "stop_evidence_missing"

    _seed_runner(session)
    with pytest.raises(ArtifactBusError) as exc2:
        _freeze(svc, ws, "c" * 64, rel)
    assert exc2.value.code == "stop_evidence_mismatch"


def test_partial_write_corruption_detected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    h = store.put_bytes(b"good-content")
    path = store._path_for(h)  # noqa: SLF001
    path.write_bytes(b"corrupted!!!!")
    assert store.verify(h) is False


def test_gc_skips_referenced_and_protects_publish_race(tmp_path: Path) -> None:
    session = _MemSession()
    _seed_runner(session)
    store = ArtifactStore(tmp_path / "store")
    svc = ArtifactService(session, store)  # type: ignore[arg-type]
    ws, evidence_sha, rel = _workspace_with_evidence(tmp_path, content="gc-me")
    cand = _freeze(svc, ws, evidence_sha, rel)
    # Orphan object: put extra blob with no candidate reference after removing protect
    orphan = store.put_bytes(b"orphan-blob")
    session.add(
        ContentObjectRow(
            content_hash=orphan,
            size_bytes=11,
            created_at=datetime.now(UTC) - timedelta(days=2),
            gc_marked_at=datetime.now(UTC) - timedelta(days=1),
            gc_protect_until=None,
        )
    )
    # Candidate content remains referenced → skipped
    result = svc.gc(GcRequest.model_validate({"graceSeconds": 0, "dryRun": False}))
    assert orphan in result.deleted
    assert cand.content_hash not in result.deleted
    assert store.has(cand.content_hash)


def test_path_escape_on_freeze(tmp_path: Path) -> None:
    # Contract RelativePath rejects .. before service; also covered by runner paths.
    with pytest.raises(Exception) as exc:
        FreezeCandidateRequest.model_validate(
            {
                "projectId": "proj_1",
                "workspaceId": "ws_1",
                "workspaceRoot": str(tmp_path),
                "relativePath": "../outside.py",
                "contractId": "ctr_1",
                "contractVersion": 1,
                "environmentHash": "b" * 64,
                "stopEvidence": {
                    "source": "runner",
                    "operationId": "rop_1",
                    "generation": 1,
                    "evidenceSha256": "a" * 64,
                    "evidencePath": "_agentforge/stop_evidence.json",
                    "writableScope": "ws_1/",
                },
            }
        )
    assert "relative" in str(exc.value).lower() or "path" in str(exc.value).lower()
