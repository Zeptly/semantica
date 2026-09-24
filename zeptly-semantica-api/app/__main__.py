"""Production entrypoint: ``python -m app``.

Binds ``[::]:${PORT}`` (dual-stack IPv6 + IPv4) so the service is reachable
over Railway private networking, which is IPv6-only in environments created
before October 2025, as well as over IPv4. If the kernel has IPv6 disabled it
falls back to ``0.0.0.0``. Railway supplies PORT; the default is 8080.
Configuration is validated before the server starts, so an unsafe posture
exits non-zero.
"""

from __future__ import annotations

import logging
import socket
import sys

import uvicorn

from .config import ConfigError, Settings
from .logging_setup import configure_logging

# Maximum concurrent connections + in-flight requests before uvicorn answers
# 503. Bounds thread/memory use under a misbehaving caller.
LIMIT_CONCURRENCY = 100

logger = logging.getLogger("zeptly_semantica")


def _bind_host() -> str:
    """Prefer a dual-stack IPv6 socket; fall back to IPv4-only."""
    if not socket.has_ipv6:
        return "0.0.0.0"  # noqa: S104 - container ingress
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            probe.bind(("::", 0))
    except OSError:
        return "0.0.0.0"  # noqa: S104 - container ingress
    return "::"


def main() -> int:
    configure_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logger.critical("refusing to start: %s", exc, extra={"event": "config_rejected"})
        return 2

    from .main import create_app

    app = create_app(settings)
    host = _bind_host()
    logger.info(
        "binding http server",
        extra={"event": "bind", "host": host, "port": settings.port},
    )
    uvicorn.run(
        app,
        host=host,
        port=settings.port,
        workers=1,
        proxy_headers=False,
        server_header=False,
        date_header=True,
        access_log=True,
        log_config=None,
        limit_concurrency=LIMIT_CONCURRENCY,
        timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
