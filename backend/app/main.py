import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from app.providers.registry import build_providers
from app.api.v1 import auth, documents, health, interviews, plans
from app.config import get_settings
from app.core.logging import register_secret, setup_logging
from app.db.session import dispose_engine
from app.ws import endpoint as ws_endpoint

log = logging.getLogger("mockmate")

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()  # fails fast on invalid config
    setup_logging(settings.log_level)
    for value in settings.secret_values():
        register_secret(value)
    app.state.providers = build_providers(settings)
    for name in ("stt", "captions"):
        warmup = getattr(getattr(app.state.providers, name), "warmup", None)
        if warmup is not None:
            await warmup()  # load speech models now so the first answer isn't slow
    log.info(
        "startup env=%s provider_mode=%s llm_mode=%s stt_mode=%s stream_asr=%s",
        settings.app_env,
        settings.provider_mode,
        settings.llm_mode,
        settings.stt_mode,
        settings.stream_asr_mode,
    )
    yield

    for name in ("llm", "tts", "captions"):
        aclose = getattr(getattr(app.state.providers, name), "aclose", None)
        if aclose is not None:
            await aclose()
    await dispose_engine()
    log.info("shutdown")
    
def create_app() -> FastAPI:
    app = FastAPI(title="MockMate", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(interviews.router, prefix="/api/v1")
    app.include_router(plans.router, prefix="/api/v1")
    app.include_router(ws_endpoint.router)  # before the static mount so /ws wins

    # Mounted last so every /api and /ws route takes precedence over static files.
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app


app = create_app()