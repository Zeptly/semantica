"""FastAPI application factory for zeptly-semantica-api."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .auth import AuthFailureCounter
from .config import Settings
from .graph import GraphError, GraphUnavailable, ProjectionGraph
from .routes import health, projection

SEMANTICA_PINNED_VERSION = "0.6.0"
MAX_BODY_BYTES = 64 * 1024

logger = logging.getLogger("zeptly_semantica")


def _check_semantica_pin() -> None:
    installed = version("semantica")
    if installed != SEMANTICA_PINNED_VERSION:
        raise RuntimeError(f"semantica=={SEMANTICA_PINNED_VERSION} is required, found {installed}")


def create_app(
    settings: Optional[Settings] = None,
    graph: Optional[ProjectionGraph] = None,
) -> FastAPI:
    _check_semantica_pin()
    settings = settings or Settings.from_env()
    graph = graph or ProjectionGraph(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "starting zeptly-semantica-api",
            extra={
                "event": "startup",
                "service_version": __version__,
                "semantica_version": version("semantica"),
                "env": settings.env,
                "auth_required": settings.auth_required,
                "anonymous_allowed": settings.allow_anonymous,
                "falkordb_host": settings.falkordb_host,
                "falkordb_port": settings.falkordb_port,
                "falkordb_password_set": settings.falkordb_password is not None,
                "graph_name": settings.graph_name,
            },
        )
        # Not fatal if FalkorDB is not up yet (Railway has no depends_on):
        # /health stays up, /ready reports 503, and every request retries
        # the connection until FalkorDB is reachable.
        ready = graph.check_ready()
        logger.info("initial readiness", extra={"event": "readiness", "ready": ready})
        yield
        logger.info(
            "shutting down",
            extra={"event": "shutdown", "auth_failures": dict(app.state.auth_failures.counts)},
        )
        graph.close()

    docs = not settings.is_production_like
    app = FastAPI(
        title="zeptly-semantica-api",
        version=__version__,
        description="Derived, non-authoritative semantic projection over Semantica GraphStore.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.graph = graph
    app.state.auth_failures = AuthFailureCounter()

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):  # type: ignore[no-untyped-def]
        length = request.headers.get("content-length")
        if length is None and request.method in ("POST", "PUT", "PATCH"):
            # Chunked bodies would bypass the size check; require a length.
            return JSONResponse({"detail": "content-length required"}, status_code=411)
        if length is not None:
            try:
                too_big = int(length) > MAX_BODY_BYTES
            except ValueError:
                return JSONResponse({"detail": "invalid content-length"}, status_code=400)
            if too_big:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(GraphUnavailable)
    async def _unavailable(_: Request, __: GraphUnavailable) -> JSONResponse:
        return JSONResponse({"detail": "graph store unavailable"}, status_code=503)

    @app.exception_handler(GraphError)
    async def _graph_error(_: Request, __: GraphError) -> JSONResponse:
        return JSONResponse({"detail": "graph operation failed"}, status_code=500)

    app.include_router(health.router)
    app.include_router(projection.router)
    return app
