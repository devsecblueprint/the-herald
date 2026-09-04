"""
Structured logging.

Every log line is one JSON object with an ``event`` key, so a run can be
followed in CloudWatch Logs Insights without parsing prose.
"""

import json
import logging
import sys
from typing import Any, Optional


class JsonFormatter(logging.Formatter):
    """Render each record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record as one line of JSON."""
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
        }
        message = record.getMessage()
        if isinstance(getattr(record, "event_payload", None), dict):
            payload.update(record.event_payload)
        else:
            payload["message"] = message
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_json_logging(level: str = "INFO", stream=None) -> logging.Logger:
    """
    Install a JSON formatter on the root logger.

    Only needed when the host application has no structured logger of its
    own; The Herald configures its own handler in ``app.main``.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    return root


class EventLogger:
    """A thin wrapper that emits named events with structured fields."""

    def __init__(self, name: str, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(name)

    def event(self, name: str, level: int = logging.INFO, **fields: Any) -> None:
        """Emit one named event with arbitrary structured fields."""
        payload = {"event": name}
        payload.update(
            {key: value for key, value in fields.items() if value is not None}
        )
        self.logger.log(
            level,
            json.dumps(payload, default=str),
            extra={"event_payload": payload},
        )

    def debug(self, name: str, **fields: Any) -> None:
        """Emit an event at DEBUG level."""
        self.event(name, level=logging.DEBUG, **fields)

    def warning(self, name: str, **fields: Any) -> None:
        """Emit an event at WARNING level."""
        self.event(name, level=logging.WARNING, **fields)

    def error(self, name: str, **fields: Any) -> None:
        """Emit an event at ERROR level."""
        self.event(name, level=logging.ERROR, **fields)
