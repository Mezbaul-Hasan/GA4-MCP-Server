"""Pagination arguments and the tool-output size budget (PROJECT.md §5).

Every list/report tool must (a) validate ``page_size``/``limit``, (b) keep its output
under ``OUTPUT_BUDGET_BYTES`` (50 KB) and (c) say explicitly when rows were dropped.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

OUTPUT_BUDGET_BYTES = 50_000


def validate_page_size(page_size: int | None, *, default: int = 50, maximum: int = 200, name: str = "page_size") -> int:
    if page_size is None:
        return default
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise ToolError(f"{name} must be an integer — got {page_size!r}")
    if page_size < 1 or page_size > maximum:
        raise ToolError(f"{name} must be between 1 and {maximum} — got {page_size}")
    return page_size


def validate_page_token(page_token: str | None) -> str | None:
    if page_token is None or page_token == "":
        return None
    if not isinstance(page_token, str) or len(page_token) > 2048 or any(c.isspace() for c in page_token):
        raise ToolError("page_token must be the exact next_page_token string returned by the previous call")
    return page_token


def json_size(obj: Any) -> int:
    """Size in bytes of ``obj`` as the MCP SDK will serialise it (indent=2)."""
    return len(json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode("utf-8"))


def enforce_output_budget(result: dict[str, Any], list_key: str, *, budget: int = OUTPUT_BUDGET_BYTES) -> dict[str, Any]:
    """Trim ``result[list_key]`` until the whole result fits in ``budget`` bytes.

    Adds ``truncated`` (bool) and, when trimming happened, ``truncation_note`` with the
    number of items kept vs. available so the model can ask for a page/filter instead.
    """
    items = result.get(list_key)
    if not isinstance(items, list):
        result.setdefault("truncated", False)
        return result
    total = len(items)
    if json_size(result) <= budget:
        result.setdefault("truncated", False)
        return result
    # Binary-search the largest prefix that fits.
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = dict(result)
        trial[list_key] = items[:mid]
        trial["truncated"] = True
        trial["truncation_note"] = f"output limited to {mid} of {total} items ({budget // 1000} KB budget) — narrow the request (filters, fewer columns, smaller limit) or page"
        if json_size(trial) <= budget:
            lo = mid
        else:
            hi = mid - 1
    kept = lo
    result[list_key] = items[:kept]
    result["truncated"] = True
    result["truncation_note"] = f"output limited to {kept} of {total} items ({budget // 1000} KB budget) — narrow the request (filters, fewer columns, smaller limit) or page"
    return result


def parse_number(value: Any) -> Any:
    """GA4 returns metric values as strings; convert to int/float when possible."""
    if isinstance(value, (int, float)) or value is None:
        return value
    s = str(value)
    try:
        if s.lstrip("-").isdigit():
            return int(s)
        return float(s)
    except ValueError:
        return value
