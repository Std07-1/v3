"""FastAPI-застосунок: API перекладу + роздача PWA-статики.

Маршрути:
  GET  /api/health     — стан сервісу.
  GET  /api/languages  — список підтримуваних мов.
  POST /api/translate  — переклад тексту.
Статика PWA монтується на '/' (у проді перед нею стоїть nginx).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__, languages
from .cache import build_cache
from .config import get_settings
from .engines import EngineError, build_engine
from .schemas import (
    HealthResponse,
    LanguageInfo,
    LanguagesResponse,
    TranslateRequest,
    TranslateResponse,
)
from .service import TranslationError, TranslationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("translate")

_FRONTEND_DIR = os.environ.get(
    "TRANSLATE_FRONTEND_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend"),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = build_engine(settings)
    cache = await build_cache(settings.redis_url, settings.cache_ttl_seconds)
    service = TranslationService(
        engine=engine,
        cache=cache,
        namespace=settings.cache_namespace,
        max_chars=settings.max_chars,
    )
    app.state.service = service
    app.state.cache = cache
    app.state.engine_name = engine.name
    logger.info("Старт: рушій=%s, кеш=%s", engine.name, cache.label)
    try:
        yield
    finally:
        await cache.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Local Translate", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            engine=app.state.engine_name,
            cache=app.state.cache.label,
            version=__version__,
        )

    @app.get("/api/languages", response_model=LanguagesResponse)
    async def list_languages() -> LanguagesResponse:
        return LanguagesResponse(
            languages=[
                LanguageInfo(
                    code=lang.code,
                    name_en=lang.name_en,
                    name_native=lang.name_native,
                )
                for lang in languages.LANGUAGES
            ]
        )

    @app.post("/api/translate", response_model=TranslateResponse)
    async def translate(req: TranslateRequest) -> TranslateResponse:
        service: TranslationService = app.state.service
        try:
            result = await service.translate(req.text, req.source, req.target)
        except TranslationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except EngineError as exc:
            logger.error("Збій рушія: %s", exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return TranslateResponse(
            text=result.text,
            source=result.source,
            target=result.target,
            engine=result.engine,
            cached=result.cached,
        )

    # PWA-статика монтується останньою, щоб не перехоплювати /api/*.
    if os.path.isdir(_FRONTEND_DIR):
        app.mount(
            "/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend"
        )

    return app


app = create_app()
