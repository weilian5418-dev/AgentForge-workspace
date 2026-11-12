"""Control workers are trusted coordinators, not ordinary queue-only workers."""

from sqlalchemy import Engine, text


def require_control_role(engine: Engine) -> None:
    with engine.connect() as connection:
        role: str = connection.execute(text("SELECT current_user")).scalar_one()
        if role != "agentforge_driver":
            raise RuntimeError(
                "Control queue requires the dedicated agentforge_driver role; never give its credentials to ordinary workers"
            )
