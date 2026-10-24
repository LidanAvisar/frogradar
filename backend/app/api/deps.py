"""Shared FastAPI dependencies for the read API."""

from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core import clock
from app.db.session import get_session


def get_now() -> datetime:
    """Request time (app/core/clock.py, the wall clock); overridden in tests."""
    return clock.now()


DbSession = Annotated[Session, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]
