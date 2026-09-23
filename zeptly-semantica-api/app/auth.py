"""Server-to-server API-key authentication for /v1 routes.

Zeptly is the authorization authority; this service only verifies that the
caller holds the shared service key. There is no user/account system here.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from .config import Settings

API_KEY_HEADER = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def _unauthorized() -> HTTPException:
    # Identical response for missing and invalid keys: do not reveal which.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing API key",
        headers={"WWW-Authenticate": API_KEY_HEADER},
    )


def require_api_key(
    request: Request,
    presented: Optional[str] = Depends(_api_key_header),
) -> None:
    settings: Settings = request.app.state.settings

    if settings.allow_anonymous:
        # Only reachable in development/test (enforced by Settings.from_env).
        return

    expected = settings.api_key
    if not expected:
        # Fail closed. Settings.from_env already refuses to start in this
        # state; this guards against programmatic misconfiguration.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication not configured",
        )

    if not presented:
        raise _unauthorized()

    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise _unauthorized()
