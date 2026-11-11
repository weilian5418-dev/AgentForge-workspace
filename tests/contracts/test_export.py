import json
from pathlib import Path

from agentforge.contracts.export import (
    SCHEMA_MODELS,
    build_contract_documents,
    export_contract_documents,
)


def test_export_is_deterministic(tmp_path: Path) -> None:
    first = build_contract_documents()
    second = build_contract_documents()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    count = export_contract_documents(tmp_path)
    assert count == len(first)
    assert (tmp_path / "openapi.json").exists()
    assert (tmp_path / "error-response.schema.json").exists()
    assert (tmp_path / "event-payloads.schema.json").exists()
    for name in SCHEMA_MODELS:
        assert (tmp_path / name).exists()


def test_openapi_only_lists_implemented_paths() -> None:
    openapi = build_contract_documents()["openapi.json"]
    paths = set(openapi["paths"])
    # Core lifecycle always present.
    assert {
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/capabilities",
        "/api/v1/control/shutdown",
    } <= paths
    # D01–D04 implemented surface (no fake placeholders).
    assert "/api/v1/providers" in paths or any(p.startswith("/api/v1/providers") for p in paths)
    assert "/api/v1/approvals" in paths
    assert "/api/v1/agent-products/discover" in paths
    assert "/api/v1/runner/profiles" in paths
    assert "/api/v1/runner/execute" in paths
    assert "/api/v1/artifacts/candidates/freeze" in paths
    assert "/api/v1/artifacts/publish" in paths
    assert "ErrorResponse" in openapi["components"]["schemas"]
    assert "/api/v1/templates" not in paths
