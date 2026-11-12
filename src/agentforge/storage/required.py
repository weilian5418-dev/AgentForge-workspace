"""Required ORM lookup: missing rows fail explicitly instead of implicit None access."""

from typing import Any

from sqlalchemy.orm import Session


def required[T](session: Session, model: type[T], identity: Any, **options: Any) -> T:
    row = session.get(model, identity, **options)
    if row is None:
        raise ValueError(f"Required {model.__name__} row not found")
    return row
