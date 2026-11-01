"""Shared plumbing for tool modules: dependency container, annotations, audit decorator."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp.types import ToolAnnotations

from marketing_mcp_common.audit import AuditLog, Timer
from marketing_mcp_common.errors import ToolError
from marketing_mcp_common.logging import get_logger

from ..client import GA4Client

log = get_logger("ga4_mcp")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


@dataclass
class Deps:
    client: GA4Client
    audit: AuditLog


def _summary(result: Any) -> dict[str, Any]:
    """Small, non-sensitive facts about a result for the audit line."""
    meta: dict[str, Any] = {}
    if isinstance(result, dict):
        for key in ("property", "returned_rows", "row_count", "count", "truncated", "next_page_token"):
            if key in result and result[key] is not None:
                meta[key] = bool(result[key]) if key == "next_page_token" else result[key]
    return meta


def audited(deps: Deps, tool_name: str) -> Callable[[F], F]:
    """Wrap a tool: log to stderr + append an audit line; keep the original signature."""

    def decorator(fn: F) -> F:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind_partial(*args, **kwargs)
            call_args = {k: v for k, v in bound.arguments.items() if v is not None}
            with Timer() as t:
                try:
                    result = await fn(*args, **kwargs)
                except ToolError as exc:
                    deps.audit.record(tool=tool_name, args=call_args, status="error", duration_ms=t.ms or 0, error=str(exc))
                    log.info("%s -> error (%s)", tool_name, str(exc)[:120])
                    raise
                except Exception as exc:  # unexpected → still audit, then surface a clean message
                    deps.audit.record(tool=tool_name, args=call_args, status="crash", duration_ms=t.ms or 0, error=f"{type(exc).__name__}: {exc}")
                    log.exception("%s crashed", tool_name)
                    raise ToolError(f"{tool_name} failed unexpectedly ({type(exc).__name__}: {exc}) — try again or simplify the request") from exc
            deps.audit.record(tool=tool_name, args=call_args, status="ok", duration_ms=t.ms, meta=_summary(result))
            log.info("%s -> ok (%.0f ms)", tool_name, t.ms)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def drop_empty(obj: Any) -> Any:
    """Recursively remove None / "" / [] / {} values to keep outputs compact."""
    if isinstance(obj, dict):
        cleaned = {k: drop_empty(v) for k, v in obj.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [drop_empty(v) for v in obj]
    return obj
