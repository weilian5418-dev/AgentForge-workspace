"""D01: provider profiles, model auth grants, budget ledger, call attempts.

Revision ID: 20261111_0008
Revises: 20261111_0007
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0008"
down_revision: str | None = "20261111_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.provider_profiles (
                id text PRIMARY KEY,
                adapter_type text NOT NULL,
                base_url text NOT NULL,
                model_id text NOT NULL,
                auth_mode text NOT NULL DEFAULT 'none',
                credential_ref text NULL,
                config_version integer NOT NULL DEFAULT 1 CHECK (config_version >= 1),
                network_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
                status text NOT NULL DEFAULT 'enabled',
                label text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.budget_accounts (
                id text PRIMARY KEY,
                scope_type text NOT NULL,
                scope_id text NOT NULL,
                currency text NOT NULL,
                soft_limit text NULL,
                hard_limit text NULL,
                token_limit integer NULL,
                settled_amount text NOT NULL DEFAULT '0',
                reserved_amount text NOT NULL DEFAULT '0',
                settled_tokens integer NOT NULL DEFAULT 0,
                reserved_tokens integer NOT NULL DEFAULT 0,
                hard_cost_enabled boolean NOT NULL DEFAULT false,
                price_version text NULL,
                account_version integer NOT NULL DEFAULT 1 CHECK (account_version >= 1),
                status text NOT NULL DEFAULT 'open',
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT uq_budget_accounts_scope UNIQUE (scope_type, scope_id, currency)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.model_authorization_grants (
                id text PRIMARY KEY,
                authorized_user text NOT NULL,
                provider_id text NOT NULL REFERENCES app.provider_profiles (id),
                provider_config_version integer NOT NULL,
                purpose text NOT NULL,
                allowed_input_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                allowed_baseline_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                max_calls integer NOT NULL CHECK (max_calls >= 1),
                calls_used integer NOT NULL DEFAULT 0 CHECK (calls_used >= 0),
                max_output_tokens integer NOT NULL,
                budget_account_id text NULL,
                grant_version integer NOT NULL DEFAULT 1 CHECK (grant_version >= 1),
                status text NOT NULL DEFAULT 'active',
                expires_at timestamptz NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                revoked_at timestamptz NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_model_auth_grants_user_status
            ON app.model_authorization_grants (authorized_user, status)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.context_manifests (
                id text PRIMARY KEY,
                grant_id text NOT NULL,
                approved_input_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                approved_baseline_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                fragment_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                content_hashes jsonb NOT NULL DEFAULT '[]'::jsonb,
                summary_strategy text NOT NULL DEFAULT 'refs_only',
                content_version integer NOT NULL DEFAULT 1,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.model_calls (
                id text PRIMARY KEY,
                provider_id text NOT NULL REFERENCES app.provider_profiles (id),
                provider_config_version integer NOT NULL,
                grant_id text NOT NULL,
                grant_version integer NOT NULL,
                purpose text NOT NULL,
                status text NOT NULL,
                settlement_status text NOT NULL DEFAULT 'none',
                request_hash text NOT NULL,
                idempotency_key text NOT NULL,
                context_manifest_id text NULL,
                reservation_id text NULL,
                result_text text NULL,
                error_code text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT uq_model_calls_idempotency UNIQUE (idempotency_key)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_model_calls_provider_status
            ON app.model_calls (provider_id, status)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.budget_reservations (
                id text PRIMARY KEY,
                account_id text NOT NULL REFERENCES app.budget_accounts (id),
                call_id text NOT NULL,
                amount text NOT NULL,
                tokens integer NOT NULL DEFAULT 0,
                status text NOT NULL DEFAULT 'active',
                account_version_at_reserve integer NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                resolved_at timestamptz NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_budget_reservations_call
            ON app.budget_reservations (call_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.model_attempts (
                id text PRIMARY KEY,
                call_id text NOT NULL REFERENCES app.model_calls (id) ON DELETE CASCADE,
                attempt_no integer NOT NULL,
                status text NOT NULL,
                request_hash text NOT NULL,
                provider_request_id text NULL,
                error_code text NULL,
                started_at timestamptz NULL,
                finished_at timestamptz NULL,
                CONSTRAINT uq_model_attempts_call_no UNIQUE (call_id, attempt_no)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.usage_records (
                id text PRIMARY KEY,
                call_id text NOT NULL,
                attempt_id text NULL,
                source text NOT NULL,
                input_tokens integer NULL,
                output_tokens integer NULL,
                total_tokens integer NULL,
                cost_amount text NULL,
                cost_currency text NULL,
                cost_estimated boolean NULL,
                price_version text NULL,
                raw_provider_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_usage_records_call
            ON app.usage_records (call_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.settlement_records (
                id text PRIMARY KEY,
                reservation_id text NOT NULL REFERENCES app.budget_reservations (id),
                kind text NOT NULL,
                amount text NOT NULL,
                tokens integer NOT NULL DEFAULT 0,
                note text NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.provider_capability_checks (
                id text PRIMARY KEY,
                provider_id text NOT NULL REFERENCES app.provider_profiles (id),
                provider_config_version integer NOT NULL,
                adapter_type text NOT NULL,
                model_id text NOT NULL,
                checked_at timestamptz NOT NULL,
                results jsonb NOT NULL,
                evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade() -> None:
    for table in (
        "provider_capability_checks",
        "settlement_records",
        "usage_records",
        "model_attempts",
        "budget_reservations",
        "model_calls",
        "context_manifests",
        "model_authorization_grants",
        "budget_accounts",
        "provider_profiles",
    ):
        op.execute(sa.text(f"DROP TABLE IF EXISTS app.{table}"))
