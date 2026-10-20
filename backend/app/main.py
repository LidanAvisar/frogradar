import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.api.health import router as health_router
from app.core.config import get_settings
from app.db.checks import EmbeddingDimMismatch, assert_embedding_dim_matches_db
from app.db.session import get_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Log loudly on a config/DB mismatch, but still start: /health must stay reachable.
    try:
        with get_engine().connect() as conn:
            assert_embedding_dim_matches_db(conn)
    except EmbeddingDimMismatch as e:
        logger.error("embedding dimension check failed: %s", e)
    except SQLAlchemyError as e:
        logger.warning("embedding dimension check skipped, DB unavailable: %s", type(e).__name__)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
