"""Unit of Work: one business operation, one commit boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


class UnitOfWork:
    """Repositories share this session and must not commit independently."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def flush(self) -> None:
        self.session.flush()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[UnitOfWork]:
    session = factory()
    uow = UnitOfWork(session)
    try:
        yield uow
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        session.close()


@contextmanager
def unit_of_work_connection(engine: Engine) -> Iterator[UnitOfWork]:
    with unit_of_work(make_session_factory(engine)) as uow:
        yield uow


def db_now(session: Session) -> Any:
    """Database clock for lease/claim expiry comparisons."""
    from sqlalchemy import text

    return session.execute(text("SELECT now()")).scalar_one()
