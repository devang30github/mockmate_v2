import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import health
from app.config import get_settings
from app.core.logging import register_secret, setup_logging
from app.db.session import dispose_engine
log = logging.getLogger("mockmate")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()  # fails fast on invalid config
    setup_logging(settings.log_level)
    for value in settings.secret_values():
        register_secret(value)

    log.info("startup env=%s provider_mode=%s", settings.app_env, settings.provider_mode)
    yield
    await dispose_engine()
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="MockMate", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api/v1")
    return app


app = create_app()