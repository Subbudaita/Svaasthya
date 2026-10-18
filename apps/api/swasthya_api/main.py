"""ASGI entry point: ``uvicorn --factory swasthya_api.main:create_app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from swasthya_ingestion.images import InvalidImageError
from swasthya_ingestion.plate_agent import PerceptionError, PerceptionRefused

from swasthya_api import __version__
from swasthya_api.budget import TokenCapExceeded
from swasthya_api.meals import MealNotFound, NotAMeal
from swasthya_api.routes import channels, health, me, meals
from swasthya_api.services import FeatureUnavailable, build_services
from swasthya_api.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = await build_services(settings)
        app.state.services = services
        app.state.telegram_gateway = None
        try:
            yield
        finally:
            await services.aclose()

    app = FastAPI(title="Swasthya API", version=__version__, lifespan=lifespan)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(TokenCapExceeded)
    async def _token_cap(_: Request, exc: TokenCapExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "daily_token_cap_reached", "cap": exc.cap, "used": exc.used},
        )

    def _error(code: int, detail: str):
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=code, content={"detail": detail, "message": str(exc)})

        return handler

    app.add_exception_handler(FeatureUnavailable, _error(503, "feature_unavailable"))
    app.add_exception_handler(NotAMeal, _error(422, "not_a_meal"))
    app.add_exception_handler(InvalidImageError, _error(422, "invalid_image"))
    app.add_exception_handler(MealNotFound, _error(404, "meal_not_found"))
    app.add_exception_handler(PerceptionRefused, _error(422, "perception_refused"))
    app.add_exception_handler(PerceptionError, _error(502, "perception_failed"))

    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(meals.router)
    app.include_router(channels.router)
    return app
