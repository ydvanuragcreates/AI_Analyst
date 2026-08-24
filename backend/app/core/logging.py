"""
Application logging setup.

Phase 1 configured plain `logging.basicConfig`. Phase 9 adds two things
without removing that: (1) every log record automatically carries
request_id/user_id from the current request context (see core/context.py)
via a logging.Filter, so correlating "everything that happened while
handling request X" is a log grep, not manual bookkeeping in every log
call; (2) a JSON formatter, used when `environment != "development"`, so
production log lines are structured (timestamp/level/request_id/user_id/
message/extra fields) for a log aggregator instead of free text.

Call sites pass additional structured fields via the standard `extra=`
kwarg, e.g. `logger.info("job started", extra={"analysis_id": ..., "event":
"analysis_started", "duration_ms": 42})` -- nothing new to learn, just
logging's own mechanism, consistently used.

Never logs: passwords, API keys, JWT tokens, raw dataset content, or
model chain-of-thought -- enforced by convention (no call site in this
codebase logs those), not by a filter that tries to scrub them after the
fact (which is far less reliable than simply never putting them in a log
call to begin with).
"""

import json
import logging

from app.core.config import get_settings
from app.core.context import get_request_id, get_user_id

_RESERVED_LOG_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class RequestContextFilter(logging.Filter):
    """Attaches the current request's request_id/user_id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.user_id = get_user_id()
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "user_id": getattr(record, "user_id", None),
        }
        # Any extra=... fields a call site passed (analysis_id, thread_id,
        # agent, event, duration_ms, ...) ride along automatically.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_FIELDS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Configure root logging handlers, level, and formatter from settings."""

    settings = get_settings()
    handler = logging.StreamHandler()
    handler.addFilter(RequestContextFilter())

    if settings.environment == "development":
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | [%(request_id)s] | %(message)s")
        )
    else:
        handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers = [handler]
