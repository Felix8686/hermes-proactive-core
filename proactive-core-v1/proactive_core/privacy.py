"""Small, deterministic redaction helpers for fixture and audit boundaries."""

from __future__ import annotations

import re
import math
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"
_MAX_TEXT = 512
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "private_chat",
    "private_chat_body",
    "private_message",
    "refresh_token",
    "secret",
    "secrets",
    "token",
}
_PRIVATE_BODY_KEYS = {
    "body",
    "chat_body",
    "chat_text",
    "content",
    "full_text",
    "full_message",
    "message",
    "message_body",
    "message_text",
    "prompt",
    "raw_message",
    "transcript",
    "user_input",
    "text",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{10,}|xox[baprs]-[A-Za-z0-9-]{8,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)(?:cookie|set-cookie)\s*:\s*[^\r\n]+"),
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE_RUN = re.compile(r"\s+")
_SAFE_STATE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,96}$")


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def sanitize_text(value: str, *, limit: int = _MAX_TEXT) -> str:
    """Redact recognizable credentials and remove control/newline injection."""
    result = _CONTROL_CHARS.sub(" ", value)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)
    result = _SPACE_RUN.sub(" ", result).strip()
    if len(result) > limit:
        result = result[:limit] + "…"
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    """Return bounded, JSON-compatible data with secrets and private bodies removed."""
    if depth > 8:
        return REDACTED
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else REDACTED
    if isinstance(value, str):
        cleaned = sanitize_text(value)
        if (
            not _SAFE_STATE_TOKEN.fullmatch(cleaned)
            or "://" in cleaned
            or cleaned.startswith("/")
            or re.match(r"^[A-Za-z]:", cleaned)
        ):
            return REDACTED
        return cleaned
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 64:
                break
            if not isinstance(key, str):
                continue
            if not _SAFE_STATE_TOKEN.fullmatch(key):
                continue
            normalized = normalize_key(key)
            if not normalized:
                continue
            segments = set(normalized.split("_"))
            sensitive = (
                normalized in _SENSITIVE_KEYS
                or bool(segments & {"token", "secret", "password", "credential", "cookie", "authorization", "key"})
                or "api_key" in normalized
            )
            private_body = (
                normalized in _PRIVATE_BODY_KEYS
                or "message" in segments
                or "transcript" in segments
                or "private_chat" in normalized
                or bool(segments & {"path", "file", "filename", "directory", "cwd", "home", "url", "uri"})
            )
            if sensitive or private_body:
                cleaned[normalized] = REDACTED
            else:
                cleaned[normalized] = sanitize_value(child, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_value(child, depth=depth + 1) for child in value[:64]]
    return REDACTED
