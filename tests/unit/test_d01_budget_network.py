"""D01 budget ledger and network policy unit tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from agentforge.contracts.providers import (
    BudgetAccountCreateRequest,
    NetworkPolicy,
    ProviderCreateRequest,
    parse_decimal_str,
)
from agentforge.models.budget import BudgetError, BudgetService, _fmt
from agentforge.models.network import NetworkPolicyError, validate_endpoint
from agentforge.storage.models import BudgetAccountRow, BudgetReservationRow


class _MemSession:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)
        ident = getattr(row, "id", None)
        if ident is not None:
            self.rows[str(ident)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: str) -> object | None:
        row = self.rows.get(key)
        if row is None:
            return None
        if model is BudgetAccountRow and isinstance(row, BudgetAccountRow):
            return row
        if model is BudgetReservationRow and isinstance(row, BudgetReservationRow):
            return row
        return row if isinstance(row, model) else None

    def scalar(self, _stmt: object) -> None:
        return None


def test_decimal_money_rejects_float_like_garbage() -> None:
    with pytest.raises(ValueError):
        parse_decimal_str("-1")
    assert parse_decimal_str("12.50") == Decimal("12.50")


def test_budget_reserve_settle_releases_remainder() -> None:
    session = _MemSession()
    svc = BudgetService(session)  # type: ignore[arg-type]
    account = svc.create_account(
        BudgetAccountCreateRequest.model_validate(
            {
                "scopeType": "global",
                "scopeId": "platform",
                "currency": "USD",
                "softLimit": "100",
                "hardCostEnabled": False,
            }
        )
    )
    # Manually wire row into get() map (create_account already added).
    row = session.added[0]
    assert isinstance(row, BudgetAccountRow)

    reservation = svc.reserve(account_id=account.account_id, call_id="c1", amount="70", tokens=0)
    assert row.reserved_amount == "70"
    svc.settle(reservation_id=reservation.reservation_id, actual_amount="25", actual_tokens=0)
    assert row.settled_amount == "25"
    assert row.reserved_amount == "0"
    # available = 100 - 25 - 0 = 75
    money, _ = svc.available(row)
    assert money == Decimal("75")


def test_hard_cost_requires_price_version() -> None:
    session = _MemSession()
    svc = BudgetService(session)  # type: ignore[arg-type]
    with pytest.raises(BudgetError) as exc:
        svc.create_account(
            BudgetAccountCreateRequest.model_validate(
                {
                    "scopeType": "run",
                    "scopeId": "run_1",
                    "currency": "USD",
                    "hardLimit": "10",
                    "hardCostEnabled": True,
                }
            )
        )
    assert exc.value.code == "price_version_required"


def test_local_policy_rejects_remote_url() -> None:
    with pytest.raises(NetworkPolicyError):
        validate_endpoint(
            "https://api.openai.com/v1",
            NetworkPolicy(network_class="local"),
        )


def test_remote_requires_https() -> None:
    with pytest.raises(NetworkPolicyError):
        validate_endpoint("http://example.com/v1", NetworkPolicy(network_class="remote"))


def test_provider_create_rejects_embedded_secrets() -> None:
    with pytest.raises(ValidationError):
        ProviderCreateRequest.model_validate(
            {
                "adapterType": "openai_compatible",
                "baseUrl": "https://user:pass@example.com/v1",
                "modelId": "gpt",
                "authMode": "none",
            }
        )


def test_fmt_decimal() -> None:
    assert _fmt(Decimal("25.00")) == "25"
    assert _fmt(Decimal("25.50")) == "25.5"
