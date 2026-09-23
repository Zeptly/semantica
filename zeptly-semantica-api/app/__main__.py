"""Production entrypoint: ``python -m app``.

Binds 0.0.0.0:${PORT} (Railway supplies PORT; default 8080). Configuration is
validated before the server starts, so an unsafe posture exits non-zero.
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from .config import ConfigError, Settings


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logging.getLogger("zeptly_semantica").critical("refusing to start: %s", exc)
        return 2

    from .main import create_app

    app = create_app(settings)
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - container ingress (Railway private/public network)
        port=settings.port,
        workers=1,
        proxy_headers=False,
        server_header=False,
        date_header=True,
        access_log=True,
        log_config=None,
        timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
