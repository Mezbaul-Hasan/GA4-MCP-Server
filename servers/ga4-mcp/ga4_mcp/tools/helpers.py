"""Composite helper tools: discovery, search, one-call health check, allow-listed raw GET."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.errors import ToolError
from marketing_mcp_common.pagination import enforce_output_budget, json_size

from .. import config, validation as v
from ._shared import READ_ONLY, Deps, audited, drop_empty
from .admin import compact_property, compact_stream
from .reports import build_report_request, compact_report


async def _all_properties(deps: Deps) -> list[dict[str, Any]]:
    """Flatten account summaries into [{account_id, account_name, property_id, display_name, property_type}]."""
    summaries = await deps.client.admin_list_all("accountSummaries", "accountSummaries", page_size=200, max_pages=20)
    rows: list[dict[str, Any]] = []
    for a in summaries:
        acc_id = (a.get("account") or "").rsplit("/", 1)[-1]
        for p in a.get("propertySummaries", []) or []:
            rows.append({
                "account_id": acc_id,
                "account_name": a.get("displayName"),
                "property_id": (p.get("property") or "").rsplit("/", 1)[-1],
                "display_name": p.get("displayName"),
                "property_type": p.get("propertyType"),
            })
    return rows


def register(mcp: MCPServer, deps: Deps) -> None:
    client = deps.client

    @mcp.tool(name="ga4_quick_overview", annotations=READ_ONLY, description=(
        "One call to see everything this Google account can access: all GA4 accounts with their properties (ids + names) and totals. "
        "Call this first in a new conversation when no property_id is known; then use the property_id with ga4_run_report."
    ))
    @audited(deps, "ga4_quick_overview")
    async def ga4_quick_overview() -> dict[str, Any]:
        rows = await _all_properties(deps)
        accounts: dict[str, dict[str, Any]] = {}
        for r in rows:
            acc = accounts.setdefault(r["account_id"], {"account_id": r["account_id"], "account_name": r["account_name"], "properties": []})
            acc["properties"].append({"property_id": r["property_id"], "display_name": r["display_name"], "property_type": r["property_type"]})
        result = {
            "account_count": len(accounts),
            "property_count": len(rows),
            "accounts": list(accounts.values()),
            "next_step": "pick a property_id and call ga4_run_report (default: last 7 days) or ga4_property_health_check",
        }
        if not rows:
            result["note"] = "this Google account has no GA4 properties — ask an admin to grant Viewer access, or sign in with another account (ga4-mcp auth)"
        return enforce_output_budget(result, "accounts", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="ga4_find_property", annotations=READ_ONLY, description=(
        "Find GA4 properties by (partial, case-insensitive) name, website/brand keyword or id, e.g. 'shop', 'example.com', '3204'. "
        "Returns matching properties with account and property_id. Use when the user names a site but not an id."
    ))
    @audited(deps, "ga4_find_property")
    async def ga4_find_property(query: str, limit: int = 20) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty search string — e.g. 'shop' or part of a property id")
        if len(query) > 200:
            raise ToolError("query is too long (max 200 characters)")
        lim = v.limit(limit, default=20, maximum=200)
        needle = query.strip().lower()
        rows = await _all_properties(deps)
        matches = [
            r for r in rows
            if needle in (r["display_name"] or "").lower() or needle in r["property_id"] or needle in (r["account_name"] or "").lower()
        ]
        result = {"query": query, "match_count": len(matches), "matches": matches[:lim], "searched_properties": len(rows)}
        if not matches:
            result["note"] = "no property matched — try a shorter keyword or call ga4_quick_overview to see all names"
        return enforce_output_budget(result, "matches", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="ga4_property_health_check", annotations=READ_ONLY, description=(
        "One-call health check of a GA4 property: settings (time zone, currency, retention), data streams, key events, traffic yesterday vs. the previous 7 days, "
        "users online right now, and a list of warnings (no data, no key events, short retention, no web stream…). "
        "Use it when onboarding a property or when someone asks 'is tracking working?'."
    ))
    @audited(deps, "ga4_property_health_check")
    async def ga4_property_health_check(property_id: str) -> dict[str, Any]:
        prop = v.property_id(property_id)
        metrics = ["activeUsers", "sessions", "screenPageViews", "eventCount", "keyEvents"]

        async def guarded(name: str, coro: Any) -> tuple[str, Any, str | None]:
            try:
                return name, await coro, None
            except ToolError as exc:
                return name, None, str(exc)

        yesterday_req = build_report_request(metrics=metrics, start_date="yesterday", end_date="yesterday", limit=1)
        week_req = build_report_request(metrics=metrics, start_date="8daysAgo", end_date="2daysAgo", limit=1)
        results = await asyncio.gather(
            guarded("property", client.admin_get(f"properties/{prop}")),
            guarded("streams", client.admin_list_all(f"properties/{prop}/dataStreams", "dataStreams", page_size=200, max_pages=2)),
            guarded("key_events", client.admin_list_all(f"properties/{prop}/keyEvents", "keyEvents", page_size=200, max_pages=2)),
            guarded("retention", client.admin_get(f"properties/{prop}/dataRetentionSettings")),
            guarded("yesterday", client.data_post(prop, "runReport", yesterday_req)),
            guarded("previous_7_days", client.data_post(prop, "runReport", week_req)),
            guarded("realtime", client.data_post(prop, "runRealtimeReport", {"metrics": [{"name": "activeUsers"}], "limit": 1})),
        )
        data: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, value, err in results:
            if err:
                errors[name] = err
            else:
                data[name] = value

        warnings: list[str] = []

        def totals(resp: dict[str, Any] | None) -> dict[str, Any]:
            if not resp:
                return {}
            rows = compact_report(resp, prop_id=prop, request={"offset": 0}).get("rows") or []
            return rows[0] if rows else {m: 0 for m in metrics}

        y = totals(data.get("yesterday"))
        w = totals(data.get("previous_7_days"))
        week_avg = {k: round(val / 7, 1) for k, val in w.items() if isinstance(val, (int, float))}
        if "yesterday" in data and not y.get("sessions") and not y.get("activeUsers"):
            warnings.append("no sessions/users recorded yesterday — tracking may be broken or data still processing")
        if week_avg.get("sessions") and y.get("sessions") is not None and y["sessions"] < 0.5 * week_avg["sessions"]:
            warnings.append(f"yesterday's sessions ({y['sessions']}) are less than half the 7-day daily average ({week_avg['sessions']})")
        key_events = data.get("key_events") or []
        if "key_events" in data and not key_events:
            warnings.append("no key events configured — nothing is being measured as a conversion")
        elif y and not y.get("keyEvents") and key_events:
            warnings.append("key events are configured but none fired yesterday")
        streams = data.get("streams") or []
        if "streams" in data and not streams:
            warnings.append("property has no data streams")
        elif streams and not any(s.get("type") == "WEB_DATA_STREAM" for s in streams):
            warnings.append("no web data stream (app-only property)")
        retention = data.get("retention") or {}
        if retention and retention.get("eventDataRetention") in ("TWO_MONTHS", None):
            warnings.append("event data retention is 2 months — raise to 14 months for explorations/comparisons")
        realtime_rows = compact_report(data["realtime"], prop_id=prop, request={"offset": 0}).get("rows") if data.get("realtime") else None
        active_now = (realtime_rows[0].get("activeUsers") if realtime_rows else 0) if data.get("realtime") else None

        result = drop_empty({
            "property": compact_property(data["property"]) if data.get("property") else {"name": f"properties/{prop}"},
            "yesterday": y,
            "previous_7_days_total": w,
            "previous_7_days_daily_average": week_avg,
            "active_users_now": active_now,
            "data_streams": [compact_stream(s) for s in streams],
            "key_events": [e.get("eventName") for e in key_events],
            "data_retention": {"event_data_retention": retention.get("eventDataRetention"), "user_data_retention": retention.get("userDataRetention")} if retention else None,
            "warnings": warnings,
            "errors": errors or None,
            "status": "problems_found" if warnings or errors else "healthy",
        })
        return result

    @mcp.tool(name="ga4_raw_get", annotations=READ_ONLY, description=(
        "Escape hatch: perform a GET request against the Google Analytics Admin or Data API for read endpoints this server does not wrap yet "
        "(e.g. 'v1beta/properties/123/firebaseLinks', 'v1alpha/properties/123/channelGroups', 'v1beta/properties/123/metadata'). "
        "Accepts a path (host chosen by api='admin'|'data') or a full https URL on analyticsadmin.googleapis.com / analyticsdata.googleapis.com. "
        "Only GET; other hosts and methods are refused. Output is raw API JSON trimmed to 50 KB."
    ))
    @audited(deps, "ga4_raw_get")
    async def ga4_raw_get(path_or_url: str, params: dict[str, Any] | None = None, api: str = "admin") -> dict[str, Any]:
        if not isinstance(path_or_url, str) or not path_or_url.strip():
            raise ToolError("path_or_url is required — e.g. 'v1beta/properties/123/firebaseLinks'")
        target = path_or_url.strip()
        if params is not None and not isinstance(params, dict):
            raise ToolError("params must be an object of query parameters, e.g. {'pageSize': 50}")
        if "://" in target:
            url = target
        else:
            if api not in ("admin", "data"):
                raise ToolError("api must be 'admin' or 'data'")
            base = config.ADMIN_API_BASE if api == "admin" else config.DATA_API_BASE
            path = target.lstrip("/")
            if not path.startswith(("v1beta/", "v1alpha/")):
                raise ToolError("path must start with 'v1beta/' or 'v1alpha/' — e.g. 'v1beta/accountSummaries'")
            if ".." in path or "?" in path or "#" in path:
                raise ToolError("path must not contain '..', '?' or '#' — pass query parameters via params")
            url = f"{base}/{path}"
        host = (urlsplit(url).hostname or "").lower()
        if host not in config.ALLOWED_HOSTS:
            raise ToolError(f"host '{host}' is not allowed — only {', '.join(sorted(config.ALLOWED_HOSTS))}")
        data = await client.raw_get(url, params=params)
        safe_url = url.split("?")[0]
        result: dict[str, Any] = {"url": safe_url, "data": data, "truncated": False}
        if json_size(result) > config.OUTPUT_BUDGET_BYTES:
            # Trim the longest list-valued field; if that is not enough, cut the JSON text.
            if isinstance(data, dict):
                list_keys = [k for k, val in data.items() if isinstance(val, list)]
                if list_keys:
                    key = max(list_keys, key=lambda k: len(data[k]))
                    trimmed = enforce_output_budget(dict(data), key, budget=config.OUTPUT_BUDGET_BYTES - 300)
                    result = {"url": safe_url, "data": trimmed, "truncated": bool(trimmed.get("truncated"))}
            if json_size(result) > config.OUTPUT_BUDGET_BYTES:
                text = json.dumps(data, ensure_ascii=False)[: config.OUTPUT_BUDGET_BYTES - 500]
                result = {"url": safe_url, "data_text_truncated": text, "truncated": True}
        return result
