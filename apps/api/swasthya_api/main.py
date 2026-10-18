"""ASGI entry point: ``uvicorn --factory swasthya_api.main:create_app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from swasthya_api import __version__
from swasthya_api.budget import TokenCapExceeded
from swasthya_api.db.session import assert_rls_enforced, create_engine
from swasthya_api.routes import health, me
from swasthya_api.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(str(settings.database_url))
        try:
            await assert_rls_enforced(engine)
            app.state.engine = engine
            yield
        finally:
            await engine.dispose()

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

    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(health.router)
    app.include_router(me.router)
    return app
