"""FastAPI application: the /api routes, plus the built React app when running in Docker."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent_api import router as agent_router
from .api import router
from .config import Settings
from .device_api import router as device_router
from .planning_api import router as planning_router
from .store import DataUnavailableError

log = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# January 2026 data never changes, so browsers may reuse a response for 5 minutes and
# Vercel's CDN for a day. Repeat visits then never reach Python at all.
CACHEABLE_PATHS = {"/api/meta", "/api/day", "/api/accuracy"}
CACHE_CONTROL = "public, max-age=300, s-maxage=86400, stale-while-revalidate=86400"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="GB Wind Forecast Monitor",
        version="0.1.0",
        description=(
            "Day-ahead view of GB national wind generation forecasts (Elexon WINDFOR) "
            "against actual outturn (Elexon FUELHH), for purchase decisions in January 2026, "
            "plus wind-aware planning of flexible demand (live, or replaying January 2026)."
        ),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(router)
    app.include_router(planning_router)
    app.include_router(agent_router)
    app.include_router(device_router)

    @app.exception_handler(DataUnavailableError)
    async def data_unavailable(request: Request, exc: DataUnavailableError) -> JSONResponse:
        # 503 tells the browser (and the CDN) this is temporary; the UI shows the message.
        log.error("Data unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Wind data is temporarily unavailable. Please try again shortly."},
        )

    @app.middleware("http")
    async def add_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in CACHEABLE_PATHS and response.status_code == 200:
            response.headers.setdefault("Cache-Control", CACHE_CONTROL)
        return response

    # In Docker one container serves both the API and the website (same origin, no CORS).
    # On Vercel the website is served by Vercel's CDN, so STATIC_DIR is not set there.
    if settings.static_dir and settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app


app = create_app()
