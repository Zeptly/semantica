"""Unauthenticated operational endpoints. They reveal no configuration,
credentials, tenant data or internal error details."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["operational"])


@router.get("/health")
def health() -> dict:
    """Process liveness. Does not touch the graph store."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: the graph store is reachable and the schema is in place."""
    if request.app.state.graph.check_ready():
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "unavailable"}, status_code=503)
