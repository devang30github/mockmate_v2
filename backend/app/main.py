import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from app.api.v1 import auth,health
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
    app.include_router(auth.router, prefix="/api/v1")

    # Mounted last so every /api route takes precedence over static files.
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app


app = create_app()