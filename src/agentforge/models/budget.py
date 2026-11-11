"""BE-06 BudgetService — single ledger for model-call reserves and settlements."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentforge.contracts.providers import (
    BudgetAccount,
    BudgetAccountCreateRequest,
    BudgetReservation,
    parse_decimal_str,
)
from agentforge.storage.models import (
    BudgetAccountRow,
    BudgetReservationRow,
    SettlementRecordRow,
)


class BudgetError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _d(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return parse_decimal_str(value)


def _fmt(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class BudgetService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_account(self, request: BudgetAccountCreateRequest) -> BudgetAccount:
        if request.hard_cost_enabled and not request.hard_limit:
            raise BudgetError("hard_limit_required", "Hard cost policy requires hardLimit")
        if request.hard_cost_enabled and not request.price_version:
            raise BudgetError(
                "price_version_required",
                "Hard cost policy requires a trusted priceVersion",
            )
        existing = self.session.scalar(
            select(BudgetAccountRow).where(
                BudgetAccountRow.scope_type == request.scope_type,
                BudgetAccountRow.scope_id == request.scope_id,
                BudgetAccountRow.currency == request.currency,
            )
        )
        if existing is not None:
            return self._to_account(existing)
        row = BudgetAccountRow(
            id=f"bud_{uuid.uuid4().hex[:16]}",
            scope_type=request.scope_type,
            scope_id=request.scope_id,
            currency=request.currency,
            soft_limit=request.soft_limit,
            hard_limit=request.hard_limit,
            token_limit=request.token_limit,
            settled_amount="0",
            reserved_amount="0",
            settled_tokens=0,
            reserved_tokens=0,
            hard_cost_enabled=request.hard_cost_enabled,
            price_version=request.price_version,
            account_version=1,
            status="open",
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        self.session.flush()
        return self._to_account(row)

    def available(self, account: BudgetAccountRow) -> tuple[Decimal | None, int | None]:
        settled = _d(account.settled_amount)
        reserved = _d(account.reserved_amount)
        limit = _d(account.hard_limit) if account.hard_limit else (
            _d(account.soft_limit) if account.soft_limit else None
        )
        money_avail = None if limit is None else (limit - settled - reserved)
        token_avail = None
        if account.token_limit is not None:
            token_avail = account.token_limit - account.settled_tokens - account.reserved_tokens
        return money_avail, token_avail

    def _lock_account(self, account_id: str) -> BudgetAccountRow:
        """Load budget account with a row lock when the dialect supports it."""
        stmt = select(BudgetAccountRow).where(BudgetAccountRow.id == account_id)
        try:
            stmt = stmt.with_for_update()
        except Exception:  # noqa: BLE001 — some test sessions ignore FOR UPDATE
            pass
        account = self.session.scalar(stmt)
        if account is None:
            # MemSession / simple get fallback
            account = self.session.get(BudgetAccountRow, account_id)
        if account is None:
            raise BudgetError("account_not_found", "Budget account not found")
        return account

    def reserve(
        self,
        *,
        account_id: str,
        call_id: str,
        amount: str,
        tokens: int,
    ) -> BudgetReservation:
        account = self._lock_account(account_id)
        if account.status != "open":
            raise BudgetError("account_blocked", "Budget account is not open")

        need = _d(amount)
        if account.hard_cost_enabled and need <= 0:
            raise BudgetError(
                "HARD_COST_UNAVAILABLE",
                "Hard cost policy refuses zero-amount reserves without trusted pricing",
            )

        expected_version = account.account_version
        money_avail, token_avail = self.available(account)
        if account.hard_cost_enabled:
            if money_avail is None or need > money_avail:
                raise BudgetError("BUDGET_EXHAUSTED", "Insufficient hard budget for reservation")
        elif money_avail is not None and need > money_avail:
            raise BudgetError("BUDGET_EXCEEDED", "Insufficient soft/estimated budget")
        if token_avail is not None and tokens > token_avail:
            raise BudgetError("BUDGET_EXHAUSTED", "Insufficient token budget")

        new_reserved_amount = _fmt(_d(account.reserved_amount) + need)
        new_reserved_tokens = account.reserved_tokens + tokens
        new_version = expected_version + 1
        self._cas_account(
            account,
            expected_version=expected_version,
            values={
                "reserved_amount": new_reserved_amount,
                "reserved_tokens": new_reserved_tokens,
                "account_version": new_version,
                "status": "open",
            },
            require_open=True,
        )

        reservation = BudgetReservationRow(
            id=f"bres_{uuid.uuid4().hex[:16]}",
            account_id=account.id,
            call_id=call_id,
            amount=_fmt(need),
            tokens=tokens,
            status="active",
            account_version_at_reserve=account.account_version,
            created_at=datetime.now(UTC),
        )
        self.session.add(reservation)
        self.session.flush()
        return self._to_reservation(reservation)

    def settle(
        self,
        *,
        reservation_id: str,
        actual_amount: str,
        actual_tokens: int,
        note: str | None = None,
    ) -> None:
        reservation = self.session.get(BudgetReservationRow, reservation_id)
        if reservation is None:
            raise BudgetError("reservation_not_found", "Reservation not found")
        if reservation.status == "settled":
            return  # idempotent
        if reservation.status not in {"active", "unresolved"}:
            raise BudgetError("invalid_reservation", f"Cannot settle {reservation.status}")

        account = self._lock_account(reservation.account_id)
        reserved = _d(reservation.amount)
        actual = _d(actual_amount)
        expected_version = account.account_version

        new_reserved_amount = _fmt(max(Decimal("0"), _d(account.reserved_amount) - reserved))
        new_reserved_tokens = max(0, account.reserved_tokens - reservation.tokens)
        new_settled_amount = _fmt(_d(account.settled_amount) + actual)
        new_settled_tokens = account.settled_tokens + actual_tokens
        new_status = account.status
        if account.hard_limit is not None and _d(new_settled_amount) > _d(account.hard_limit):
            new_status = "blocked"

        self._cas_account(
            account,
            expected_version=expected_version,
            values={
                "reserved_amount": new_reserved_amount,
                "reserved_tokens": new_reserved_tokens,
                "settled_amount": new_settled_amount,
                "settled_tokens": new_settled_tokens,
                "account_version": expected_version + 1,
                "status": new_status,
            },
        )

        reservation.status = "settled"
        reservation.resolved_at = datetime.now(UTC)
        self.session.add(
            SettlementRecordRow(
                id=f"setl_{uuid.uuid4().hex[:16]}",
                reservation_id=reservation.id,
                kind="settle",
                amount=_fmt(actual),
                tokens=actual_tokens,
                note=note,
            )
        )
        self.session.flush()

    def release_undispatched(self, reservation_id: str, *, note: str) -> None:
        """Only when ledger proves dispatch never started."""
        reservation = self.session.get(BudgetReservationRow, reservation_id)
        if reservation is None:
            raise BudgetError("reservation_not_found", "Reservation not found")
        if reservation.status != "active":
            return
        account = self._lock_account(reservation.account_id)
        expected_version = account.account_version
        new_reserved_amount = _fmt(
            max(Decimal("0"), _d(account.reserved_amount) - _d(reservation.amount))
        )
        new_reserved_tokens = max(0, account.reserved_tokens - reservation.tokens)
        self._cas_account(
            account,
            expected_version=expected_version,
            values={
                "reserved_amount": new_reserved_amount,
                "reserved_tokens": new_reserved_tokens,
                "account_version": expected_version + 1,
            },
        )
        reservation.status = "released"
        reservation.resolved_at = datetime.now(UTC)
        self.session.add(
            SettlementRecordRow(
                id=f"setl_{uuid.uuid4().hex[:16]}",
                reservation_id=reservation.id,
                kind="settle",
                amount="0",
                tokens=0,
                note=note,
            )
        )
        self.session.flush()

    def _cas_account(
        self,
        account: BudgetAccountRow,
        *,
        expected_version: int,
        values: dict[str, object],
        require_open: bool = False,
    ) -> None:
        """Atomic account_version CAS; falls back for in-memory test sessions."""
        where = [
            BudgetAccountRow.id == account.id,
            BudgetAccountRow.account_version == expected_version,
        ]
        if require_open:
            where.append(BudgetAccountRow.status == "open")
        try:
            result = self.session.execute(
                update(BudgetAccountRow).where(*where).values(**values)
            )
            if getattr(result, "rowcount", 1) == 0:
                raise BudgetError("BUDGET_CONFLICT", "Concurrent budget update; retry")
        except BudgetError:
            raise
        except Exception:
            # _MemSession and similar: apply in-process if version still matches.
            if account.account_version != expected_version:
                raise BudgetError("BUDGET_CONFLICT", "Concurrent budget update; retry") from None
            if require_open and account.status != "open":
                raise BudgetError("account_blocked", "Budget account is not open") from None
        for key, value in values.items():
            setattr(account, key, value)

    def mark_unresolved(self, reservation_id: str, *, note: str) -> None:
        reservation = self.session.get(BudgetReservationRow, reservation_id)
        if reservation is None:
            raise BudgetError("reservation_not_found", "Reservation not found")
        if reservation.status in {"settled", "released"}:
            return
        # Keep risk reserve — do not TTL-auto-release after possible dispatch.
        reservation.status = "unresolved"
        self.session.add(
            SettlementRecordRow(
                id=f"setl_{uuid.uuid4().hex[:16]}",
                reservation_id=reservation.id,
                kind="mark_unresolved",
                amount=reservation.amount,
                tokens=reservation.tokens,
                note=note,
            )
        )
        self.session.flush()

    def _to_account(self, row: BudgetAccountRow) -> BudgetAccount:
        return BudgetAccount(
            account_id=row.id,
            scope_type=row.scope_type,
            scope_id=row.scope_id,
            currency=row.currency,
            soft_limit=row.soft_limit,
            hard_limit=row.hard_limit,
            token_limit=row.token_limit,
            settled_amount=row.settled_amount,
            reserved_amount=row.reserved_amount,
            settled_tokens=row.settled_tokens,
            reserved_tokens=row.reserved_tokens,
            hard_cost_enabled=row.hard_cost_enabled,
            price_version=row.price_version,
            account_version=row.account_version,
            status=row.status,  # type: ignore[arg-type]
            created_at=row.created_at,
        )

    def _to_reservation(self, row: BudgetReservationRow) -> BudgetReservation:
        return BudgetReservation(
            reservation_id=row.id,
            account_id=row.account_id,
            call_id=row.call_id,
            amount=row.amount,
            tokens=row.tokens,
            status=row.status,  # type: ignore[arg-type]
            account_version_at_reserve=row.account_version_at_reserve,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
        )
