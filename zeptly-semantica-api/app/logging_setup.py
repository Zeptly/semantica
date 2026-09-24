"""Minimal structured (JSON-lines) logging on the standard library.

Railway parses one JSON object per line and uses the ``level`` and
``message`` fields. Only the fields below plus explicit ``extra=`` keys are
emitted; request bodies and headers are never logged anywhere in the service.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Attributes every LogRecord has; anything else was passed via extra=.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured duplicate of message
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            # Exception type only: messages from the graph driver can embed
            # query text/parameters.
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # semantica's own loggers are chatty at INFO (per-connection messages).
    logging.getLogger("semantica").setLevel(logging.WARNING)
