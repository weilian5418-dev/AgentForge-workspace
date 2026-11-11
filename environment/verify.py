"""Environment smoke checks; no application service or production migration."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "environment" / "results"


def command(args: list[str], timeout: int = 60) -> str:
    process = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {args[0]}")
    return process.stdout.decode("utf-8", errors="replace").strip()


def packages() -> dict:
    modules = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "procrastinate",
        "langgraph.graph",
        "langgraph.checkpoint.postgres",
        "langchain",
        "deepagents",
        "httpx",
        "typer",
        "pptx",
        "markdown_it",
        "ruamel.yaml",
        "pytest",
        "pytest_asyncio",
        "ruff",
        "mypy",
        "structlog",
        "opentelemetry.sdk",
        "prometheus_client",
        "mcp",
        "tree_sitter",
    ]
    for module in modules:
        importlib.import_module(module)
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "fastapi",
            "pydantic",
            "sqlalchemy",
            "psycopg",
            "alembic",
            "procrastinate",
            "langgraph",
            "langgraph-checkpoint-postgres",
            "langchain",
            "deepagents",
            "pytest",
            "python-pptx",
            "ruff",
            "mypy",
        )
    }
    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "imports": len(modules),
        "versions": versions,
    }


def api_probe() -> dict:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel, ConfigDict, Field

    class Request(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: int = Field(ge=0, strict=True)

    app = FastAPI()

    # Resolve the local class explicitly because this module postpones annotations.
    def echo(payload):
        return {"value": payload.value}

    echo.__annotations__["payload"] = Request
    app.post("/echo")(echo)
    with TestClient(app) as client:
        assert client.post("/echo", json={"value": 7}).json() == {"value": 7}
        assert client.post("/echo", json={"value": "7"}).status_code == 422
        assert client.get("/openapi.json").status_code == 200
    return {"request_validation": "passed", "openapi": "passed"}


class ProbeState(TypedDict):
    value: int


def database_probe(container: str) -> dict:
    import procrastinate
    import psycopg
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, StateGraph
    from psycopg import sql
    from psycopg.rows import dict_row
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL

    # Capture only required credentials in memory. Never print or persist them.
    raw = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            r'printf "%s\000%s\000%s" "$POSTGRES_USER" "${POSTGRES_DB:-$POSTGRES_USER}" "$POSTGRES_PASSWORD"',
        ],
        capture_output=True,
        check=True,
        timeout=15,
    ).stdout
    user, dbname, password = raw.decode().split("\x00", 2)
    connection: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 5432,
        "user": user,
        "password": password,
        "dbname": dbname,
        "connect_timeout": 10,
    }
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host="127.0.0.1",
            port=5432,
            database=dbname,
        )
    )
    try:
        with engine.connect() as connection_sa:
            assert connection_sa.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
    conninfo = psycopg.conninfo.make_conninfo(**connection)
    suffix = uuid.uuid4().hex[:12]
    schemas = [f"af_envcheck_queue_{suffix}", f"af_envcheck_graph_{suffix}"]
    created: list[str] = []
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as admin:
        before = admin.execute(
            "SELECT count(*) AS n FROM pg_tables WHERE schemaname = 'app'"
        ).fetchone()
        assert before is not None
        before = before["n"]
        version_row = admin.execute("SHOW server_version").fetchone()
        assert version_row is not None
        version = version_row["server_version"]
        try:
            for schema in schemas:
                admin.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(schema)))
                created.append(schema)

            async def queue_probe():
                conninfo = psycopg.conninfo.make_conninfo(**connection)
                queue_name = f"envcheck_{suffix}"
                connector = procrastinate.PsycopgConnector(
                    conninfo=conninfo,
                    min_size=1,
                    max_size=2,
                    kwargs={"options": f"-c search_path={schemas[0]},pg_catalog"},
                )
                app = procrastinate.App(connector=connector)
                seen = []

                @app.task(name=f"environment.echo_{suffix}", queue=queue_name)
                async def echo(value: int):
                    seen.append(value)

                async with app.open_async():
                    await app.schema_manager.apply_schema_async()
                    job_id = await echo.defer_async(value=42)
                    await asyncio.wait_for(
                        app.run_worker_async(
                            queues=[queue_name],
                            wait=False,
                            concurrency=1,
                            listen_notify=False,
                            install_signal_handlers=False,
                            delete_jobs="never",
                        ),
                        timeout=30,
                    )
                    assert seen == [42]
                    return job_id

            factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
            with asyncio.Runner(loop_factory=factory) as runner:
                job_id = runner.run(queue_probe())
            job_status = admin.execute(
                sql.SQL(
                    "SELECT status::text AS status FROM {}.procrastinate_jobs WHERE id = %s"
                ).format(sql.Identifier(schemas[0])),
                (job_id,),
            ).fetchone()
            assert job_status is not None
            job_status = job_status["status"]
            assert job_status == "succeeded"

            graph_conninfo = psycopg.conninfo.make_conninfo(
                conninfo, options=f"-c search_path={schemas[1]},pg_catalog"
            )
            builder = StateGraph(ProbeState)
            builder.add_node("increment", lambda state: {"value": state["value"] + 1})
            builder.add_edge(START, "increment")
            builder.add_edge("increment", END)
            config: RunnableConfig = {"configurable": {"thread_id": f"envcheck-{suffix}"}}
            with psycopg.connect(
                graph_conninfo, autocommit=True, row_factory=dict_row
            ) as graph_connection:
                saver = PostgresSaver(graph_connection)
                saver.setup()
                graph = builder.compile(checkpointer=saver)
                assert graph.invoke({"value": 1}, config)["value"] == 2
            with psycopg.connect(graph_conninfo, autocommit=True, row_factory=dict_row) as reopened:
                graph = builder.compile(checkpointer=PostgresSaver(reopened))
                assert graph.get_state(config).values["value"] == 2
        finally:
            # Drop only schemas created successfully by this invocation.
            for schema in reversed(created):
                assert schema in schemas and schema.endswith(suffix)
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        after = admin.execute(
            "SELECT count(*) AS n FROM pg_tables WHERE schemaname = 'app'"
        ).fetchone()
        assert after is not None
        after = after["n"]
        assert before == after
    return {
        "database": dbname,
        "server_version": version,
        "sqlalchemy": "passed",
        "queue_job": "succeeded",
        "checkpoint_reopen": "passed",
        "temporary_schemas_removed": True,
        "app_table_count_unchanged": after,
        "scope": "development compatibility smoke, not production role/recovery certification",
    }


def artifact_probe(image: str | None) -> dict:
    from markdown_it import MarkdownIt
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches, Pt

    assert MarkdownIt().parse("# AgentForge\n\nEnvironment ready.")[0].type == "heading_open"
    output = RESULTS / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(0.8), Inches(1.0), Inches(11.5), Inches(2.0))
    paragraph = box.text_frame.paragraphs[0]
    paragraph.text = "AgentForge 环境验证"
    paragraph.font.size = Pt(36)
    paragraph.font.name = "Noto Sans CJK SC"
    slide.notes_slide.notes_text_frame.text = "Python 3.13 / PostgreSQL / PPTX render smoke"
    file = output / "environment-smoke.pptx"
    presentation.save(str(file))
    restored = Presentation(str(file))
    assert len(restored.slides) == 1
    assert "Python 3.13" in restored.slides[0].notes_slide.notes_text_frame.text
    result: dict[str, Any] = {"pptx_roundtrip_notes": "passed", "markdown_parse": "passed"}
    if image:
        script = (
            "mkdir -p /tmp/home && "
            "libreoffice -env:UserInstallation=file:///tmp/lo-profile --headless "
            "--convert-to pdf --outdir /work /work/environment-smoke.pptx && "
            "pdftoppm -png -r 96 -singlefile /work/environment-smoke.pdf /work/environment-smoke && "
            "pdfinfo /work/environment-smoke.pdf && rg --version && git --version"
        )
        info = command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "768m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                "--mount",
                f"type=bind,source={output},target=/work",
                image,
                "sh",
                "-ec",
                script,
            ],
            timeout=120,
        )
        assert (output / "environment-smoke.pdf").stat().st_size > 500
        with Image.open(output / "environment-smoke.png") as picture:
            assert picture.width > 100 and picture.height > 100
            result["render_size"] = list(picture.size)
        result["render"] = "passed"
        result["renderer_output"] = info
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-container")
    parser.add_argument("--renderer-image")
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"timestamp": datetime.now(UTC).isoformat(), "status": "passed"}
    checks = [("packages", packages), ("api", api_probe)]
    if args.database_container:
        checks.append(("database", lambda: database_probe(args.database_container)))
    checks.append(("artifacts", lambda: artifact_probe(args.renderer_image)))
    for name, check in checks:
        try:
            report[name] = check()
            print(f"{name}: passed", flush=True)
        except Exception as exc:  # noqa: BLE001 -- redact secrets from third-party failures
            # Never emit database connection strings, environment values or tracebacks.
            report[name] = {"status": "failed", "error_type": type(exc).__name__}
            report["status"] = "failed"
            print(f"{name}: failed ({type(exc).__name__})", flush=True)
    (RESULTS / "verification.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
