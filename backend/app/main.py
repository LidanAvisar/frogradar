import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.agents.mission.worker import get_worker
from app.api.ask import router as ask_router
from app.api.battlecards import router as battlecards_router
from app.api.brief import router as brief_router
from app.api.entities import router as entities_router
from app.api.health import router as health_router
from app.api.missions import router as missions_router
from app.api.radar import router as radar_router
from app.api.review import router as review_router
from app.api.signals import router as signals_router
from app.core.config import get_settings
from app.db.checks import EmbeddingDimMismatch, assert_embedding_dim_matches_db
from app.db.session import get_engine
from app.mcp.http import mount_mcp

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Log loudly on a config/DB mismatch, but still start: /health must stay reachable.
    try:
        with get_engine().connect() as conn:
            assert_embedding_dim_matches_db(conn)
    except EmbeddingDimMismatch as e:
        logger.error("embedding dimension check failed: %s", e)
    except SQLAlchemyError as e:
        logger.warning("embedding dimension check skipped, DB unavailable: %s", type(e).__name__)
    worker = None
    if get_settings().mission_worker:
        try:
            worker = get_worker()
            worker.start()  # also fails missions a restart interrupted (D71)
        except SQLAlchemyError as e:
            logger.warning("mission worker not started, DB unavailable: %s", type(e).__name__)
            worker = None
    async with AsyncExitStack() as stack:
        mcp_server = getattr(app.state, "mcp", None)
        if mcp_server is not None:  # the /mcp route needs its session manager running
            await stack.enter_async_context(mcp_server.session_manager.run())
        yield
    if worker is not None:
        worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(brief_router)
    app.include_router(signals_router)
    app.include_router(entities_router)
    app.include_router(battlecards_router)
    app.include_router(radar_router)
    app.include_router(ask_router)
    app.include_router(review_router)
    app.include_router(missions_router)
    app.state.mcp = mount_mcp(app, get_settings())
    return app


app = create_app()
