"""Input validation for GA4 tools — every value is checked before it reaches a URL."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from .config import DEFAULT_REPORT_LIMIT, MAX_REPORT_LIMIT

_NUMERIC = re.compile(r"^\d{1,20}$")
_API_NAME = re.compile(r"^[A-Za-z0-9_:.]{1,100}$")
_RELATIVE_DATE = re.compile(r"^(today|yesterday|\d{1,4}daysAgo)$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE = re.compile(r"^\d{8}$")


def _numeric_id(value: Any, *, prefix: str, label: str, example: str) -> str:
    """Accept ``123``, ``"123"`` or ``"<prefix>/123"``; return the bare numeric id."""
    if isinstance(value, bool):
        raise ToolError(f"{label} must be a numeric id — e.g. {example}")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise ToolError(f"{label} must be a numeric id — e.g. {example}")
    s = value.strip()
    if s.startswith(prefix + "/"):
        s = s[len(prefix) + 1 :]
    if not _NUMERIC.match(s):
        raise ToolError(f"{label} must be numeric (optionally prefixed with '{prefix}/') — got {value!r}; e.g. {example}")
    return s


def property_id(value: Any) -> str:
    return _numeric_id(value, prefix="properties", label="property_id", example="'320448620' or 'properties/320448620'")


def account_id(value: Any) -> str:
    return _numeric_id(value, prefix="accounts", label="account_id", example="'123456' or 'accounts/123456'")


def stream_id(value: Any) -> str:
    if isinstance(value, str) and "/dataStreams/" in value:
        value = value.rsplit("/dataStreams/", 1)[1]
    return _numeric_id(value, prefix="dataStreams", label="stream_id", example="'987654321'")


def property_name(value: Any) -> str:
    return f"properties/{property_id(value)}"


def account_name(value: Any) -> str:
    return f"accounts/{account_id(value)}"


def api_name(value: Any, *, label: str) -> str:
    """Dimension/metric API names: letters, digits, '_' and ':' (``keyEvents:purchase``)."""
    if not isinstance(value, str) or not _API_NAME.match(value.strip()):
        raise ToolError(f"{label} must be a GA4 API name like 'sessions' or 'keyEvents:purchase' — got {value!r}")
    return value.strip()


def api_names(values: Any, *, label: str, minimum: int = 0, maximum: int = 10) -> list[str]:
    if values is None:
        values = []
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\s]+", values) if v]
    if not isinstance(values, list):
        raise ToolError(f"{label} must be a list of names — e.g. ['sessions', 'activeUsers']")
    if len(values) < minimum:
        raise ToolError(f"{label}: at least {minimum} required — e.g. ['sessions']")
    if len(values) > maximum:
        raise ToolError(f"{label}: GA4 allows at most {maximum} per request — got {len(values)}")
    return [api_name(v, label=label) for v in values]


def report_date(value: Any, *, label: str) -> str:
    """Accept ``YYYY-MM-DD``, ``YYYYMMDD``, ``today``, ``yesterday``, ``NdaysAgo``."""
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{label} must be a date string — e.g. '2026-10-01', 'today', 'yesterday', '7daysAgo'")
    s = value.strip()
    if _RELATIVE_DATE.match(s):
        return s
    if _COMPACT_DATE.match(s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if _ISO_DATE.match(s):
        try:
            date.fromisoformat(s)
        except ValueError as exc:
            raise ToolError(f"{label} is not a valid calendar date: {s}") from exc
        return s
    raise ToolError(f"{label} must be 'YYYY-MM-DD', 'today', 'yesterday' or 'NdaysAgo' — got {value!r}")


def date_ranges(start_date: Any, end_date: Any, extra: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    ranges = [{"startDate": report_date(start_date, label="start_date"), "endDate": report_date(end_date, label="end_date")}]
    for i, item in enumerate(extra or []):
        if not isinstance(item, dict) or "start_date" not in item and "startDate" not in item:
            raise ToolError("date_ranges items must look like {'start_date': '2026-09-01', 'end_date': '2026-09-30', 'name': 'september'}")
        rng = {
            "startDate": report_date(item.get("start_date", item.get("startDate")), label=f"date_ranges[{i}].start_date"),
            "endDate": report_date(item.get("end_date", item.get("endDate", "yesterday")), label=f"date_ranges[{i}].end_date"),
        }
        if item.get("name"):
            rng["name"] = str(item["name"])[:50]
        ranges.append(rng)
    if len(ranges) > 4:
        raise ToolError("GA4 allows at most 4 date ranges per report")
    return ranges


def limit(value: Any, *, default: int = DEFAULT_REPORT_LIMIT, maximum: int = MAX_REPORT_LIMIT) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"limit must be an integer between 1 and {maximum}")
    if value < 1 or value > maximum:
        raise ToolError(f"limit must be between 1 and {maximum} — got {value} (use offset to page through more rows)")
    return value


def offset(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolError("offset must be an integer >= 0")
    return value


def optional_dict(value: Any, *, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ToolError(f"{label} must be a JSON object following the GA4 Data API schema")
    return value


def optional_list_of_dicts(value: Any, *, label: str, maximum: int = 20) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ToolError(f"{label} must be a list of JSON objects following the GA4 Data API schema")
    if len(value) > maximum:
        raise ToolError(f"{label}: at most {maximum} items")
    return value
