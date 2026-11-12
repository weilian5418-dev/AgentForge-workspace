"""Validated process configuration; credentials never appear in repr or responses."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

RunMode = Literal["dev", "local", "hosted"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTFORGE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    state_dir: Path = Path(".agentforge")
    run_mode: RunMode = "dev"
    api_token: SecretStr | None = Field(default=None, repr=False)
    database_url: SecretStr | None = Field(default=None, repr=False)
    # Driver/worker URL with queue schema DML. API role alone cannot enqueue.
    queue_database_url: SecretStr | None = Field(default=None, repr=False)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    expected_alembic_revision: str = "20261111_0013"

    @field_validator("api_token")
    @classmethod
    def valid_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if (
                len(token) < 32
                or len(token) > 512
                or not token.isascii()
                or any(c.isspace() for c in token)
            ):
                raise ValueError("API token must be 32-512 non-whitespace ASCII characters")
        return value

    @staticmethod
    def _validate_pg_url(value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                url = make_url(value.get_secret_value())
                if url.drivername != "postgresql+psycopg" or not url.database:
                    raise ValueError
            except (ValueError, ArgumentError):
                raise ValueError("Use a postgresql+psycopg URL with an existing database") from None
        return value

    @field_validator("database_url")
    @classmethod
    def valid_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        return cls._validate_pg_url(value)

    @field_validator("queue_database_url")
    @classmethod
    def valid_queue_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        return cls._validate_pg_url(value)

    def require_queue_database_url(self) -> SecretStr:
        """URL for Procrastinate defer/work — must have queue schema privileges."""
        if self.queue_database_url is not None:
            return self.queue_database_url
        raise ValueError(
            "Set AGENTFORGE_QUEUE_DATABASE_URL to a driver/worker role with queue DML; "
            "AGENTFORGE_DATABASE_URL (API role) cannot access schema queue"
        )

    @model_validator(mode="after")
    def absolute_state_dir(self) -> "Settings":
        object.__setattr__(self, "state_dir", self.state_dir.expanduser().resolve())
        return self

    @property
    def token_path(self) -> Path:
        return self.state_dir / "credentials" / "api-token"

    @property
    def credential_registry_path(self) -> Path:
        return self.state_dir / "credentials" / "registry.json"

    @property
    def runtime_dir(self) -> Path:
        return self.state_dir / "runtime"

    @property
    def runtime_record_path(self) -> Path:
        return self.runtime_dir / "instance.json"

    @property
    def control_path(self) -> Path:
        return self.runtime_dir / "control.json"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "logs" / "api.log"

    @property
    def base_url(self) -> str:
        address = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{address}:{self.port}"
