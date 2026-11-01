"""Remove tokens and secrets from strings and mappings before logging or auditing.

The patterns cover Google access tokens (``ya29.…``), Google refresh tokens (``1//…``),
Google OAuth client secrets (``GOCSPX-…``), ``Bearer`` headers and any ``key=value`` /
``"key": "value"`` pair whose key looks secret. Platform servers may extend
``SECRET_KEYS`` for their own vocabulary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

# Keys whose values must never appear in logs/audit lines (case-insensitive, substring match).
SECRET_KEYS: tuple[str, ...] = (
    "client_secret",
    "refresh_token",
    "access_token",
    "id_token",
    "authorization",
    "developer_token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "code_verifier",
)

_TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ya29\.[0-9A-Za-z\-_\.]+"), "ya29." + REDACTED),
    (re.compile(r"1//[0-9A-Za-z\-_]{20,}"), "1//" + REDACTED),
    (re.compile(r"GOCSPX-[0-9A-Za-z\-_]+"), "GOCSPX-" + REDACTED),
    (re.compile(r"(?i)\bbearer\s+[0-9A-Za-z\-_\.=]+"), "Bearer " + REDACTED),
    # key=value or "key": "value" forms for secret-looking keys
    (
        re.compile(
            r"(?i)(\"?(?:client_secret|refresh_token|access_token|id_token|code_verifier|"
            r"developer_token|api_key|password|secret)\"?\s*[:=]\s*\"?)([^\s\"&,}]+)"
        ),
        r"\1" + REDACTED,
    ),
)


def redact(text: Any) -> str:
    """Return ``text`` (coerced to ``str``) with secrets replaced by ``[REDACTED]``."""
    s = text if isinstance(text, str) else str(text)
    for pattern, replacement in _TOKEN_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(marker in k for marker in SECRET_KEYS)


def redact_mapping(data: Any, *, max_str_len: int = 200) -> Any:
    """Deep-copy ``data`` replacing secret values; long strings are shortened.

    Works on nested dicts/lists; other values are returned unchanged (after string redaction).
    """
    if isinstance(data, Mapping):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if is_secret_key(str(key)):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = redact_mapping(value, max_str_len=max_str_len)
        return out
    if isinstance(data, (list, tuple)):
        return [redact_mapping(v, max_str_len=max_str_len) for v in data]
    if isinstance(data, str):
        s = redact(data)
        if len(s) > max_str_len:
            s = s[: max_str_len - 1] + "…"
        return s
    return data
