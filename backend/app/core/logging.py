import json
import logging
import re
import threading
from datetime import datetime, timezone

_PATTERNS = [
    # Bearer tokens first, so the scheme word is never mistaken for the secret
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-~+/]+=*"), "Bearer [REDACTED]"),
    # key=value / "key": "value" for sensitive-looking keys; an auth scheme
    # word (Bearer/Basic) after the key is skipped so the real value is caught
    (
        re.compile(
            r"(?i)((?:authorization|api[-_]?key|secret|password|token|cookie)"
            r"[\"']?\s*[:=]\s*[\"']?(?:(?:bearer|basic)\s+)?)([^\s\"',}]+)"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[REDACTED]"),
]

_registered: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Exact secret values are scrubbed wherever they appear."""
    if value and len(value) >= 6:
        with _lock:
            _registered.add(value)


def redact(text: str) -> str:
    with _lock:
        known = sorted(_registered, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, "[REDACTED]")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())