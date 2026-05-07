from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import json
import logging
import sys
from threading import Lock
from typing import Any

_LOGGER_NAME = "evidenceplane"
_DECISION_COUNTS: Counter[str] = Counter()
_COUNTER_LOCK = Lock()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_evidenceplane_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root._evidenceplane_configured = True


def log_event(event: str, **fields: Any) -> None:
    logging.getLogger(_LOGGER_NAME).info(
        event,
        extra={"event": event, "fields": fields},
    )


def record_policy_decision(decision: str) -> dict[str, int]:
    with _COUNTER_LOCK:
        _DECISION_COUNTS[decision] += 1
        return dict(_DECISION_COUNTS)
