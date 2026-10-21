"""Shared FastAPI dependencies for the read API."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_session


def get_now() -> datetime:
    """Request time; overridden in tests to pin "today"."""
    return datetime.now(UTC)


DbSession = Annotated[Session, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]
