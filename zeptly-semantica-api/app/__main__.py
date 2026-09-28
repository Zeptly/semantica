"""Production entrypoint: ``python -m app``.

Binds ``0.0.0.0:${PORT}`` by default. Railway supplies PORT (default 8080)
and its healthcheck reaches the container over IPv4; environments created on
or after 2025-10-16 also have dual-stack private networking, so IPv4 serves
both.

``SEMANTICA_BIND_HOST=::`` is for legacy Railway environments whose private
network is IPv6-only. In that mode the listening socket is created here with
``IPV6_V6ONLY=0`` and handed to uvicorn, so it is dual-stack regardless of
the runtime's ``net.ipv6.bindv6only`` default. (Passing ``host="::"`` to
uvicorn instead would inherit that default; on Railway that produced an
IPv6-only listener that the IPv4 healthcheck could never reach.)

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


def dual_stack_socket(port: int) -> socket.socket:
    """A listening-ready TCP socket on [::]:port that also accepts IPv4."""
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
    except OSError:
        sock.close()
        raise
    return sock


def main() -> int:
    configure_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logger.critical("refusing to start: %s", exc, extra={"event": "config_rejected"})
        return 2

    from .main import create_app

    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.bind_host,
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
    server = uvicorn.Server(config)

    if settings.bind_host == "::":
        try:
            sock = dual_stack_socket(settings.port)
        except OSError as exc:
            logger.critical(
                "cannot bind dual-stack socket: %s", exc, extra={"event": "bind_failed"}
            )
            return 2
        v6only = sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
        logger.info(
            "binding http server",
            extra={
                "event": "bind",
                "host": "::",
                "port": settings.port,
                "ipv6_only": bool(v6only),
            },
        )
        server.run(sockets=[sock])
    else:
        logger.info(
            "binding http server",
            extra={"event": "bind", "host": settings.bind_host, "port": settings.port},
        )
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
