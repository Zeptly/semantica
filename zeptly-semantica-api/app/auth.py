"""Server-to-server API-key authentication for /v1 routes.

Zeptly is the authorization authority; this service only verifies that the
caller holds the shared service key. There is no user/account system here.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading

from fastapi import HTTPException, Request, status

from .config import Settings

API_KEY_HEADER = "X-API-Key"

logger = logging.getLogger("zeptly_semantica.auth")


class AuthFailureCounter:
    """Process-local count of rejected requests, by reason. Never records
    credential material - only why the request was rejected."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {"missing": 0, "invalid": 0, "ambiguous": 0}

    def record(self, reason: str) -> int:
        with self._lock:
            self.counts[reason] = self.counts.get(reason, 0) + 1
            return sum(self.counts.values())


def _digest(value: str) -> bytes:
    # Compare fixed-length digests so the comparison time does not depend on
    # the presented key's length or content.
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).digest()


def _reject(request: Request, reason: str) -> HTTPException:
    counter: AuthFailureCounter = request.app.state.auth_failures
    total = counter.record(reason)
    route = request.scope.get("route")
    logger.warning(
        "authentication rejected",
        extra={
            "event": "auth_rejected",
            "reason": reason,
            "method": request.method,
            "route": getattr(
                route, "path", None
            ),  # template, e.g. /v1/workspaces/{workspace_id}/...
            "auth_failures_total": total,
        },
    )
    # Identical response for every failure mode: do not reveal which.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing API key",
        headers={"WWW-Authenticate": API_KEY_HEADER},
    )


def require_api_key(request: Request) -> None:
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

    presented = request.headers.getlist(API_KEY_HEADER)
    if len(presented) > 1:
        # Proxies/frameworks disagree on which duplicate wins; refuse to guess.
        raise _reject(request, "ambiguous")
    if not presented or not presented[0]:
        raise _reject(request, "missing")
    if not hmac.compare_digest(_digest(presented[0]), _digest(expected)):
        raise _reject(request, "invalid")
