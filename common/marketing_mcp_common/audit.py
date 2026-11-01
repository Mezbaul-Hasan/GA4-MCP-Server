"""Local audit log: one JSON line per tool call (PROJECT.md §6 / §6.1 rule 1).

Stored next to the credentials (``~/.<app>/audit.jsonl``, mode 0600). Arguments are
redacted and shortened; results are never stored — only status, duration and an
optional small ``meta`` dict (e.g. row counts, property id).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging import get_logger
from .redact import redact, redact_mapping


class AuditLog:
    def __init__(self, path: Path, *, enabled: bool = True, max_bytes: int = 20 * 1024 * 1024) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.max_bytes = max_bytes
        self._log = get_logger("marketing_mcp.audit")

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                backup = self.path.with_suffix(".jsonl.1")
                os.replace(self.path, backup)
        except OSError:
            pass

    def record(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None,
        status: str,
        duration_ms: float,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
        user: str | None = None,
    ) -> None:
        """Append one line; failures to write never break the tool call."""
        if not self.enabled:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "args": redact_mapping(args or {}),
        }
        if user:
            entry["user"] = user
        if error:
            entry["error"] = redact(error)[:300]
        if meta:
            entry["meta"] = redact_mapping(meta)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            self._log.warning("audit log write failed: %s", exc)


class Timer:
    """``with Timer() as t: ...; t.ms``"""

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = (time.monotonic() - self._start) * 1000
