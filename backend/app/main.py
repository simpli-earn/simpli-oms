import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.database import make_engine, make_session_factory
from app.models import Base
from app.routers import orders, portfolio, strategies
from app.services.broker import AlpacaBroker, Broker
from app.services.sync import sync_once

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, broker: Broker | None = None) -> FastAPI:
    settings = settings or Settings()
    if broker is None:
        broker = AlpacaBroker(settings)
    engine = make_engine(settings.oms_database_url)
    session_factory = make_session_factory(engine)
    Base.metadata.create_all(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if app.state.settings.oms_sync_enabled:
            task = asyncio.create_task(_sync_loop(app))
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Simpli OMS",
        summary="Order management for one Alpaca paper account.",
        description=(
            "External callers send fully specified equity, single-leg option, and multi-leg "
            "option orders. Every order includes a strategy_id that was registered first. "
            "Alpaca performs execution. Authenticate with the X-API-Key header."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.broker = broker
    app.state.session_factory = session_factory
    origins = [item.strip() for item in settings.oms_cors_origins.split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(strategies.router)
    app.include_router(orders.router)
    app.include_router(portfolio.router)

    @app.get("/api/health", tags=["system"])
    def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "simpli-oms", "docs": "/docs"}

    return app


async def _sync_loop(app: FastAPI) -> None:
    interval = app.state.settings.oms_sync_interval_seconds
    while True:
        try:
            await asyncio.to_thread(sync_once, app.state.session_factory, app.state.broker)
        except Exception:
            logger.exception("order sync failed")
        await asyncio.sleep(interval)


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


_configure_logging()
app = create_app()
