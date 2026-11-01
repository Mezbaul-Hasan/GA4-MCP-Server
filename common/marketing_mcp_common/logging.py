"""Stderr-only logging with automatic secret redaction.

stdio MCP servers own stdout for the protocol stream, so **everything** we log must go to
stderr. Every formatted record passes through :func:`marketing_mcp_common.redact.redact`.
"""

from __future__ import annotations

import logging
import os
import sys

from .redact import redact

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - stdlib override
        return redact(super().format(record))


def get_logger(name: str, *, level: str | None = None) -> logging.Logger:
    """Return a logger writing to stderr with redaction.

    ``level`` defaults to the ``MCP_LOG_LEVEL`` environment variable or ``INFO``.
    Calling it twice for the same name does not duplicate handlers.
    """
    logger = logging.getLogger(name)
    if not any(getattr(h, "_marketing_mcp", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(RedactingFormatter(_FORMAT))
        handler._marketing_mcp = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel((level or os.environ.get("MCP_LOG_LEVEL") or "INFO").upper())
    return logger
