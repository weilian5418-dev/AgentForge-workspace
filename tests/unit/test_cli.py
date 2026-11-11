from typer.testing import CliRunner

from agentforge.cli.main import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_init_and_doctor_do_not_print_secret(monkeypatch):
    secret = "test-secret-" + "x" * 48
    monkeypatch.setenv("AGENTFORGE_API_TOKEN", secret)
    assert runner.invoke(app, ["init"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "unconfigured" in result.stdout
    assert secret not in result.stdout
    assert runner.invoke(app, ["doctor", "--require-database"]).exit_code == 3


def test_invalid_config_does_not_echo_secret(monkeypatch):
    monkeypatch.setenv("AGENTFORGE_DATABASE_URL", "invalid-private-password")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 2
    assert "invalid-private-password" not in result.output


def test_export_contracts_without_credentials(tmp_path):
    from agentforge.contracts.export import SCHEMA_MODELS

    output = tmp_path / "schemas"
    result = runner.invoke(app, ["export-contracts", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert (output / "openapi.json").is_file()
    assert (output / "event-payloads.schema.json").is_file()
    # openapi + event-payloads + one file per SCHEMA_MODELS entry
    assert len(list(output.glob("*.json"))) == len(SCHEMA_MODELS) + 2
    assert "Exported" in result.stdout
