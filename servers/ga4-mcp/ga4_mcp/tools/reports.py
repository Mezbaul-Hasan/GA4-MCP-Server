"""Data API tools (reports) — all read-only.

Request building, filter shortcuts and compact output live here so that
``ga4_run_report``, ``ga4_batch_run_reports``, ``ga4_run_pivot_report`` and the helper
tools all produce the same row shape: ``[{dimension: value, metric: number}, …]``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.errors import ToolError
from marketing_mcp_common.pagination import enforce_output_budget, parse_number

from .. import config, validation as v
from ._shared import READ_ONLY, Deps, audited, drop_empty

_FILTER_KEYS = {"filter", "andGroup", "orGroup", "notExpression"}
_MATCH_TYPES = {"EXACT", "BEGINS_WITH", "ENDS_WITH", "CONTAINS", "FULL_REGEXP", "PARTIAL_REGEXP"}


# ----------------------------------------------------------------------------- request building
def normalize_filter(expr: Any, *, kind: str) -> dict[str, Any] | None:
    """Accept a full GA4 ``FilterExpression`` or the shortcut ``{"field": value}``.

    Shortcut rules: a string value → ``stringFilter EXACT``; a list → ``inListFilter``;
    a number (metric filters) → ``numericFilter EQUAL``; a string starting with ``~`` →
    ``CONTAINS`` (case-insensitive); ``>=``, ``<=``, ``>``, ``<`` prefixes on metric values →
    numeric comparisons. Several keys are AND-ed.
    """
    if expr is None:
        return None
    if not isinstance(expr, dict):
        raise ToolError(f"{kind}_filter must be a JSON object — e.g. {{'country': 'Bangladesh'}} or a full GA4 FilterExpression")
    if not expr:
        return None
    if _FILTER_KEYS & set(expr.keys()):
        return expr  # already a FilterExpression
    clauses: list[dict[str, Any]] = []
    for field, value in expr.items():
        field = v.api_name(field, label=f"{kind}_filter field")
        clauses.append({"filter": {"fieldName": field, **_leaf_filter(value, field=field, kind=kind)}})
    if len(clauses) == 1:
        return clauses[0]
    return {"andGroup": {"expressions": clauses}}


def _numeric_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        raise ToolError("metric filter values must be numbers")
    if isinstance(value, int):
        return {"int64Value": str(value)}
    return {"doubleValue": float(value)}


def _leaf_filter(value: Any, *, field: str, kind: str) -> dict[str, Any]:
    if isinstance(value, list):
        if not value:
            raise ToolError(f"filter for '{field}' has an empty list")
        return {"inListFilter": {"values": [str(x) for x in value], "caseSensitive": False}}
    if isinstance(value, bool):
        return {"stringFilter": {"matchType": "EXACT", "value": str(value).lower()}}
    if isinstance(value, (int, float)):
        if kind == "metric":
            return {"numericFilter": {"operation": "EQUAL", "value": _numeric_value(value)}}
        return {"stringFilter": {"matchType": "EXACT", "value": str(value)}}
    if isinstance(value, dict):
        return value  # raw leaf (stringFilter / numericFilter / betweenFilter / inListFilter)
    s = str(value)
    ops = (("<=", "LESS_THAN_OR_EQUAL"), (">=", "GREATER_THAN_OR_EQUAL"), ("<", "LESS_THAN"), (">", "GREATER_THAN"))
    if kind == "metric":
        for prefix, op in ops:
            if s.startswith(prefix):
                num = parse_number(s[len(prefix):].strip())
                if isinstance(num, str):
                    raise ToolError(f"metric filter for '{field}' needs a number after '{prefix}' — got {s!r}")
                return {"numericFilter": {"operation": op, "value": _numeric_value(num)}}
        num = parse_number(s)
        if not isinstance(num, str):
            return {"numericFilter": {"operation": "EQUAL", "value": _numeric_value(num)}}
    if s.startswith("~"):
        return {"stringFilter": {"matchType": "CONTAINS", "value": s[1:], "caseSensitive": False}}
    if s.startswith("^"):
        return {"stringFilter": {"matchType": "BEGINS_WITH", "value": s[1:], "caseSensitive": False}}
    if s.startswith("re:"):
        return {"stringFilter": {"matchType": "FULL_REGEXP", "value": s[3:], "caseSensitive": False}}
    return {"stringFilter": {"matchType": "EXACT", "value": s, "caseSensitive": False}}


def normalize_order_by(order_by: Any, *, metrics: list[str], dimensions: list[str]) -> list[dict[str, Any]] | None:
    """``"-sessions"`` → metric desc, ``"country"`` → dimension asc, or a raw OrderBy list."""
    if order_by is None or order_by == "":
        return None
    if isinstance(order_by, list):
        if all(isinstance(o, dict) for o in order_by):
            return order_by
        if all(isinstance(o, str) for o in order_by):
            out: list[dict[str, Any]] = []
            for o in order_by:
                out.extend(normalize_order_by(o, metrics=metrics, dimensions=dimensions) or [])
            return out
        raise ToolError("order_by must be a string like '-sessions' or a list of GA4 OrderBy objects")
    if not isinstance(order_by, str):
        raise ToolError("order_by must be a string like '-sessions' or a list of GA4 OrderBy objects")
    desc = order_by.startswith("-")
    name = v.api_name(order_by.lstrip("-+"), label="order_by")
    if name in metrics:
        return [{"metric": {"metricName": name}, "desc": desc}]
    if name in dimensions:
        return [{"dimension": {"dimensionName": name}, "desc": desc}]
    raise ToolError(f"order_by '{name}' must be one of the requested metrics {metrics} or dimensions {dimensions}")


def build_report_request(
    *,
    metrics: Any,
    dimensions: Any = None,
    start_date: Any = config.DEFAULT_START_DATE,
    end_date: Any = config.DEFAULT_END_DATE,
    date_ranges: Any = None,
    dimension_filter: Any = None,
    metric_filter: Any = None,
    order_by: Any = None,
    limit: Any = None,
    offset: Any = None,
    currency_code: Any = None,
    keep_empty_rows: bool = False,
    metric_aggregations: Any = None,
    return_property_quota: bool = False,
) -> dict[str, Any]:
    mets = v.api_names(metrics, label="metrics", minimum=1, maximum=10)
    dims = v.api_names(dimensions, label="dimensions", minimum=0, maximum=9)
    body: dict[str, Any] = {
        "metrics": [{"name": m} for m in mets],
        "dimensions": [{"name": d} for d in dims],
        "dateRanges": v.date_ranges(start_date, end_date, v.optional_list_of_dicts(date_ranges, label="date_ranges", maximum=3)),
        "limit": v.limit(limit),
        "offset": v.offset(offset),
        "keepEmptyRows": bool(keep_empty_rows),
        "returnPropertyQuota": bool(return_property_quota),
    }
    dim_f = normalize_filter(dimension_filter, kind="dimension")
    met_f = normalize_filter(metric_filter, kind="metric")
    if dim_f:
        body["dimensionFilter"] = dim_f
    if met_f:
        body["metricFilter"] = met_f
    order = normalize_order_by(order_by, metrics=mets, dimensions=dims)
    if order:
        body["orderBys"] = order
    if currency_code:
        if not isinstance(currency_code, str) or len(currency_code) != 3:
            raise ToolError("currency_code must be a 3-letter ISO code like 'USD' or 'BDT'")
        body["currencyCode"] = currency_code.upper()
    if metric_aggregations:
        allowed = {"TOTAL", "MINIMUM", "MAXIMUM", "COUNT"}
        aggs = [str(a).upper() for a in (metric_aggregations if isinstance(metric_aggregations, list) else [metric_aggregations])]
        bad = [a for a in aggs if a not in allowed]
        if bad:
            raise ToolError(f"metric_aggregations must be from {sorted(allowed)} — got {bad}")
        body["metricAggregations"] = aggs
    return body


# ----------------------------------------------------------------------------- response compaction
def _rows(resp: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    dim_headers = [h.get("name", "") for h in resp.get("dimensionHeaders", [])]
    met_headers = [h.get("name", "") for h in resp.get("metricHeaders", [])]
    rows: list[dict[str, Any]] = []
    for r in resp.get("rows", []) or []:
        row: dict[str, Any] = {}
        for name, cell in zip(dim_headers, r.get("dimensionValues", []) or []):
            row[name] = cell.get("value")
        for name, cell in zip(met_headers, r.get("metricValues", []) or []):
            row[name] = parse_number(cell.get("value"))
        rows.append(row)
    return dim_headers, met_headers, rows


def _aggregate_rows(resp: dict[str, Any], key: str, met_headers: list[str]) -> list[dict[str, Any]] | None:
    items = resp.get(key)
    if not items:
        return None
    out = []
    for r in items:
        entry: dict[str, Any] = {}
        for cell in r.get("dimensionValues", []) or []:
            if cell.get("value") not in (None, "RESERVED_TOTAL", "RESERVED_MIN", "RESERVED_MAX", "RESERVED_COUNT"):
                entry.setdefault("dimension_values", []).append(cell.get("value"))
        for name, cell in zip(met_headers, r.get("metricValues", []) or []):
            entry[name] = parse_number(cell.get("value"))
        out.append(entry)
    return out


def _quota(resp: dict[str, Any]) -> dict[str, Any] | None:
    q = resp.get("propertyQuota")
    if not q:
        return None
    return {k: {"consumed": v_.get("consumed"), "remaining": v_.get("remaining")} for k, v_ in q.items() if isinstance(v_, dict)}


def _metadata(resp: dict[str, Any]) -> dict[str, Any]:
    md = resp.get("metadata") or {}
    out = {
        "currency_code": md.get("currencyCode"),
        "time_zone": md.get("timeZone"),
        "subject_to_thresholding": md.get("subjectToThresholding"),
        "data_loss_from_other_row": md.get("dataLossFromOtherRow"),
        "empty_reason": md.get("emptyReason"),
    }
    if md.get("samplingMetadatas"):
        out["sampling"] = [
            {"samples_read": parse_number(s.get("samplesReadCount")), "sampling_space": parse_number(s.get("samplingSpaceSize"))}
            for s in md["samplingMetadatas"]
        ]
    if md.get("schemaRestrictionResponse", {}).get("activeMetricRestrictions"):
        out["restricted_metrics"] = md["schemaRestrictionResponse"]["activeMetricRestrictions"]
    return drop_empty(out)


def compact_report(resp: dict[str, Any], *, prop_id: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
    dim_headers, met_headers, rows = _rows(resp)
    offset = int((request or {}).get("offset") or 0)
    row_count = int(resp.get("rowCount") or 0)
    returned = len(rows)
    next_offset = offset + returned if row_count and offset + returned < row_count else None
    result: dict[str, Any] = {
        "property": f"properties/{prop_id}",
        "date_ranges": (request or {}).get("dateRanges"),
        "dimensions": dim_headers,
        "metrics": met_headers,
        "rows": rows,
        "row_count": row_count,
        "returned_rows": returned,
        "offset": offset,
        "next_offset": next_offset,
        "totals": _aggregate_rows(resp, "totals", met_headers),
        "maximums": _aggregate_rows(resp, "maximums", met_headers),
        "minimums": _aggregate_rows(resp, "minimums", met_headers),
        "metadata": _metadata(resp),
        "quota": _quota(resp),
    }
    if not rows:
        reason = (resp.get("metadata") or {}).get("emptyReason")
        result["note"] = reason or "no rows matched — check the date range (reports are in the property's time zone) and filters; data for today/yesterday may still be processing"
    result = {k: val for k, val in result.items() if val is not None and val != {}}
    return enforce_output_budget(result, "rows", budget=config.OUTPUT_BUDGET_BYTES)


def compact_pivot_report(resp: dict[str, Any], *, prop_id: str, request: dict[str, Any]) -> dict[str, Any]:
    dim_headers, met_headers, rows = _rows(resp)
    pivots = []
    for ph in resp.get("pivotHeaders", []) or []:
        values = []
        for pdh in ph.get("pivotDimensionHeaders", []) or []:
            values.append([cell.get("value") for cell in pdh.get("dimensionValues", []) or []])
        pivots.append({"row_count": ph.get("rowCount"), "values": values})
    result: dict[str, Any] = {
        "property": f"properties/{prop_id}",
        "date_ranges": request.get("dateRanges"),
        "dimensions": dim_headers,
        "metrics": met_headers,
        "pivot_headers": pivots,
        "rows": rows,
        "returned_rows": len(rows),
        "aggregates": _aggregate_rows(resp, "aggregates", met_headers),
        "metadata": _metadata(resp),
        "quota": _quota(resp),
    }
    result = {k: val for k, val in result.items() if val is not None and val != {}}
    return enforce_output_budget(result, "rows", budget=config.OUTPUT_BUDGET_BYTES)


def compact_metadata(resp: dict[str, Any], *, kind: str, search: str | None, custom_only: bool, include_descriptions: bool) -> dict[str, Any]:
    needle = (search or "").lower().strip()

    def keep(item: dict[str, Any]) -> bool:
        if custom_only and not item.get("customDefinition"):
            return False
        if not needle:
            return True
        hay = " ".join(str(item.get(k, "")) for k in ("apiName", "uiName", "description", "category")).lower()
        return needle in hay

    def shape(item: dict[str, Any], is_metric: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"api_name": item.get("apiName"), "ui_name": item.get("uiName"), "category": item.get("category")}
        if is_metric:
            out["type"] = item.get("type")
            if item.get("expression"):
                out["expression"] = item["expression"]
            if item.get("blockedReasons"):
                out["blocked_reasons"] = item["blockedReasons"]
        if item.get("customDefinition"):
            out["custom"] = True
        if item.get("deprecatedApiNames"):
            out["deprecated_api_names"] = item["deprecatedApiNames"]
        if include_descriptions or needle:
            desc = str(item.get("description") or "")
            if desc:
                out["description"] = desc[:160] + ("…" if len(desc) > 160 else "")
        return drop_empty(out)

    dims = [shape(d, False) for d in resp.get("dimensions", []) or [] if keep(d)] if kind in ("all", "dimensions") else []
    mets = [shape(m, True) for m in resp.get("metrics", []) or [] if keep(m)] if kind in ("all", "metrics") else []
    result: dict[str, Any] = {
        "name": resp.get("name"),
        "dimension_count": len(dims),
        "metric_count": len(mets),
        "dimensions": dims,
        "metrics": mets,
        "note": "metric names: use 'keyEvents' (not 'conversions'); traffic sources are 'sessionSource'/'sessionMedium'/'sessionDefaultChannelGroup' (session scope) or 'firstUser…' (user scope)",
    }
    # Trim the bigger list first, then the other one if still over budget.
    first, second = ("metrics", "dimensions") if len(mets) >= len(dims) else ("dimensions", "metrics")
    result = enforce_output_budget(result, first, budget=config.OUTPUT_BUDGET_BYTES)
    if result.get("truncated"):
        result = enforce_output_budget(result, second, budget=config.OUTPUT_BUDGET_BYTES)
        result["truncated"] = True
        result["truncation_note"] = "catalogue is long — call again with kind='dimensions' or kind='metrics' and a search term"
    return result


def compact_compatibility(resp: dict[str, Any]) -> dict[str, Any]:
    def shape(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        out = []
        for it in items or []:
            meta = it.get(key) or {}
            out.append({"api_name": meta.get("apiName"), "compatibility": it.get("compatibility")})
        return out

    dims = shape(resp.get("dimensionCompatibilities", []), "dimensionMetadata")
    mets = shape(resp.get("metricCompatibilities", []), "metricMetadata")
    incompatible = [x["api_name"] for x in dims + mets if x.get("compatibility") == "INCOMPATIBLE"]
    return {
        "dimensions": dims,
        "metrics": mets,
        "incompatible": incompatible,
        "all_compatible": not incompatible,
    }


# ----------------------------------------------------------------------------- tool registration
def register(mcp: MCPServer, deps: Deps) -> None:
    client = deps.client

    @mcp.tool(name="ga4_run_report", annotations=READ_ONLY, description=(
        "Run a GA4 report (Data API runReport). The main reporting tool. "
        "Args: property_id ('320448620' or 'properties/320448620'); metrics (1–10, e.g. ['sessions','activeUsers','keyEvents','totalRevenue']); "
        "dimensions (0–9, e.g. ['date'], ['sessionDefaultChannelGroup'], ['pagePath'], ['country','deviceCategory']); "
        "start_date/end_date ('2026-10-01', 'today', 'yesterday', '7daysAgo' — default last 7 days ending yesterday); "
        "date_ranges = extra comparison ranges [{'start_date':'14daysAgo','end_date':'8daysAgo','name':'previous'}] (adds a 'dateRange' dimension); "
        "dimension_filter shortcut {'country':'Bangladesh'} / {'pagePath':'~blog'} (contains) / {'deviceCategory':['mobile','tablet']} or a full GA4 FilterExpression; "
        "metric_filter shortcut {'sessions':'>100'}; order_by '-sessions' (desc) or 'date'; limit (default 100, max 1000) + offset for paging; "
        "metric_aggregations ['TOTAL'] adds totals. Returns rows as [{dimension: value, metric: number}], row_count (total matching rows), next_offset, metadata "
        "(currency, time zone, thresholding). Use ga4_get_metadata to discover valid names; use ga4_run_realtime_report for the last 30 minutes. "
        "Note: the metric is 'keyEvents' (GA4 renamed conversions); per-event: 'keyEvents:purchase'."
    ))
    @audited(deps, "ga4_run_report")
    async def ga4_run_report(
        property_id: str,
        metrics: list[str],
        dimensions: list[str] | None = None,
        start_date: str = config.DEFAULT_START_DATE,
        end_date: str = config.DEFAULT_END_DATE,
        date_ranges: list[dict[str, Any]] | None = None,
        dimension_filter: dict[str, Any] | None = None,
        metric_filter: dict[str, Any] | None = None,
        order_by: str | list[Any] | None = None,
        limit: int = config.DEFAULT_REPORT_LIMIT,
        offset: int = 0,
        currency_code: str | None = None,
        keep_empty_rows: bool = False,
        metric_aggregations: list[str] | None = None,
        return_property_quota: bool = False,
    ) -> dict[str, Any]:
        prop = v.property_id(property_id)
        body = build_report_request(
            metrics=metrics, dimensions=dimensions, start_date=start_date, end_date=end_date, date_ranges=date_ranges,
            dimension_filter=dimension_filter, metric_filter=metric_filter, order_by=order_by, limit=limit, offset=offset,
            currency_code=currency_code, keep_empty_rows=keep_empty_rows, metric_aggregations=metric_aggregations,
            return_property_quota=return_property_quota,
        )
        resp = await client.data_post(prop, "runReport", body)
        return compact_report(resp, prop_id=prop, request=body)

    @mcp.tool(name="ga4_run_realtime_report", annotations=READ_ONLY, description=(
        "Who is on the site right now (Data API runRealtimeReport, last 30 minutes; separate quota). "
        "metrics e.g. ['activeUsers'], ['screenPageViews'], ['eventCount'], ['keyEvents']; dimensions e.g. ['country'], ['unifiedScreenName'], ['deviceCategory'], ['minutesAgo']. "
        "Optional minute_ranges [{'name':'last5','start_minutes_ago':5,'end_minutes_ago':0}] (max 2, up to 60 minutes back for GA4 360). "
        "Returns rows like ga4_run_report. Do NOT use for historical questions — use ga4_run_report."
    ))
    @audited(deps, "ga4_run_realtime_report")
    async def ga4_run_realtime_report(
        property_id: str,
        metrics: list[str],
        dimensions: list[str] | None = None,
        dimension_filter: dict[str, Any] | None = None,
        metric_filter: dict[str, Any] | None = None,
        order_by: str | list[Any] | None = None,
        limit: int = config.DEFAULT_REPORT_LIMIT,
        minute_ranges: list[dict[str, Any]] | None = None,
        return_property_quota: bool = False,
    ) -> dict[str, Any]:
        prop = v.property_id(property_id)
        mets = v.api_names(metrics, label="metrics", minimum=1, maximum=10)
        dims = v.api_names(dimensions, label="dimensions", minimum=0, maximum=9)
        body: dict[str, Any] = {
            "metrics": [{"name": m} for m in mets],
            "dimensions": [{"name": d} for d in dims],
            "limit": v.limit(limit),
            "returnPropertyQuota": bool(return_property_quota),
        }
        if (f := normalize_filter(dimension_filter, kind="dimension")):
            body["dimensionFilter"] = f
        if (f := normalize_filter(metric_filter, kind="metric")):
            body["metricFilter"] = f
        if (o := normalize_order_by(order_by, metrics=mets, dimensions=dims)):
            body["orderBys"] = o
        if minute_ranges:
            ranges = v.optional_list_of_dicts(minute_ranges, label="minute_ranges", maximum=2) or []
            body["minuteRanges"] = [
                drop_empty({
                    "name": r.get("name"),
                    "startMinutesAgo": r.get("start_minutes_ago", r.get("startMinutesAgo")),
                    "endMinutesAgo": r.get("end_minutes_ago", r.get("endMinutesAgo")),
                })
                for r in ranges
            ]
        resp = await client.data_post(prop, "runRealtimeReport", body)
        result = compact_report(resp, prop_id=prop, request={"offset": 0})
        result["window"] = "realtime (last 30 minutes)" if not minute_ranges else "realtime (custom minute ranges)"
        return result

    @mcp.tool(name="ga4_run_pivot_report", annotations=READ_ONLY, description=(
        "Cross-tab report (Data API runPivotReport): rows of one dimension pivoted by others, e.g. sessions by date × deviceCategory. "
        "pivots = [{'field_names':['date'],'limit':31,'order_by':'-sessions'}, {'field_names':['deviceCategory'],'limit':5}] — every requested dimension must appear in exactly one pivot. "
        "Same metrics/dimensions/date/filter arguments as ga4_run_report. Prefer ga4_run_report unless you really need a pivot."
    ))
    @audited(deps, "ga4_run_pivot_report")
    async def ga4_run_pivot_report(
        property_id: str,
        metrics: list[str],
        dimensions: list[str],
        pivots: list[dict[str, Any]],
        start_date: str = config.DEFAULT_START_DATE,
        end_date: str = config.DEFAULT_END_DATE,
        dimension_filter: dict[str, Any] | None = None,
        metric_filter: dict[str, Any] | None = None,
        currency_code: str | None = None,
        keep_empty_rows: bool = False,
        return_property_quota: bool = False,
    ) -> dict[str, Any]:
        prop = v.property_id(property_id)
        mets = v.api_names(metrics, label="metrics", minimum=1, maximum=10)
        dims = v.api_names(dimensions, label="dimensions", minimum=1, maximum=9)
        piv_in = v.optional_list_of_dicts(pivots, label="pivots", maximum=4) or []
        if not piv_in:
            raise ToolError("pivots is required — e.g. [{'field_names': ['date'], 'limit': 31}]")
        piv_out = []
        for i, p in enumerate(piv_in):
            names = p.get("field_names", p.get("fieldNames"))
            fields = v.api_names(names, label=f"pivots[{i}].field_names", minimum=1, maximum=4)
            unknown = [f for f in fields if f not in dims]
            if unknown:
                raise ToolError(f"pivots[{i}].field_names {unknown} must be in dimensions {dims}")
            entry: dict[str, Any] = {"fieldNames": fields, "limit": v.limit(p.get("limit"), default=10, maximum=250)}
            if p.get("offset"):
                entry["offset"] = v.offset(p.get("offset"))
            if p.get("order_by", p.get("orderBys")) is not None:
                entry["orderBys"] = normalize_order_by(p.get("order_by", p.get("orderBys")), metrics=mets, dimensions=fields)
            if p.get("metric_aggregations"):
                entry["metricAggregations"] = [str(a).upper() for a in p["metric_aggregations"]]
            piv_out.append(entry)
        body: dict[str, Any] = {
            "metrics": [{"name": m} for m in mets],
            "dimensions": [{"name": d} for d in dims],
            "dateRanges": v.date_ranges(start_date, end_date, None),
            "pivots": piv_out,
            "keepEmptyRows": bool(keep_empty_rows),
            "returnPropertyQuota": bool(return_property_quota),
        }
        if (f := normalize_filter(dimension_filter, kind="dimension")):
            body["dimensionFilter"] = f
        if (f := normalize_filter(metric_filter, kind="metric")):
            body["metricFilter"] = f
        if currency_code:
            body["currencyCode"] = str(currency_code).upper()
        resp = await client.data_post(prop, "runPivotReport", body)
        return compact_pivot_report(resp, prop_id=prop, request=body)

    @mcp.tool(name="ga4_batch_run_reports", annotations=READ_ONLY, description=(
        "Run up to 5 reports for one property in a single call (Data API batchRunReports) — e.g. channel breakdown + top pages + daily trend at once. "
        "requests = list of objects with the same keys as ga4_run_report (metrics, dimensions, start_date, end_date, dimension_filter, metric_filter, order_by, limit, offset, name). "
        "Returns {'reports': [{name, ...same shape as ga4_run_report}]}. Each report counts against quota separately."
    ))
    @audited(deps, "ga4_batch_run_reports")
    async def ga4_batch_run_reports(property_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        prop = v.property_id(property_id)
        reqs = v.optional_list_of_dicts(requests, label="requests", maximum=config.MAX_BATCH_REPORTS) or []
        if not reqs:
            raise ToolError("requests must contain 1–5 report definitions — e.g. [{'metrics': ['sessions'], 'dimensions': ['date']}]")
        bodies = []
        names = []
        for i, r in enumerate(reqs):
            names.append(str(r.get("name") or f"report_{i + 1}"))
            allowed = {
                "metrics", "dimensions", "start_date", "end_date", "date_ranges", "dimension_filter", "metric_filter",
                "order_by", "limit", "offset", "currency_code", "keep_empty_rows", "metric_aggregations", "return_property_quota",
            }
            unknown = set(r) - allowed - {"name"}
            if unknown:
                raise ToolError(f"requests[{i}] has unknown keys {sorted(unknown)} — allowed: {sorted(allowed)}")
            if "metrics" not in r:
                raise ToolError(f"requests[{i}] needs 'metrics'")
            bodies.append(build_report_request(**{k: r[k] for k in r if k in allowed}))
        per_request_budget = max(8_000, config.OUTPUT_BUDGET_BYTES // len(bodies))
        resp = await client.data_post(prop, "batchRunReports", {"requests": bodies})
        reports = []
        for name, body, rep in zip(names, bodies, resp.get("reports", []) or []):
            compact = compact_report(rep, prop_id=prop, request=body)
            compact = enforce_output_budget(compact, "rows", budget=per_request_budget)
            compact["name"] = name
            reports.append(compact)
        return {"property": f"properties/{prop}", "count": len(reports), "reports": reports}

    @mcp.tool(name="ga4_get_metadata", annotations=READ_ONLY, description=(
        "List the dimensions and metrics available to a property (Data API getMetadata), including custom definitions and key-event metrics. "
        "Use it to find correct API names before ga4_run_report. kind: 'all' | 'dimensions' | 'metrics'; search: substring filter on name/description "
        "(e.g. 'revenue', 'channel', 'keyEvents'); custom_only: only custom dimensions/metrics; include_descriptions adds short descriptions. "
        "property_id '0' returns the standard (non-custom) catalogue."
    ))
    @audited(deps, "ga4_get_metadata")
    async def ga4_get_metadata(
        property_id: str,
        kind: str = "all",
        search: str | None = None,
        custom_only: bool = False,
        include_descriptions: bool = False,
    ) -> dict[str, Any]:
        prop = v.property_id(property_id)
        if kind not in ("all", "dimensions", "metrics"):
            raise ToolError("kind must be 'all', 'dimensions' or 'metrics'")
        resp = await client.data_get(f"properties/{prop}/metadata")
        return compact_metadata(resp, kind=kind, search=search, custom_only=custom_only, include_descriptions=include_descriptions)

    @mcp.tool(name="ga4_check_compatibility", annotations=READ_ONLY, description=(
        "Check whether a set of dimensions and metrics can be combined in one report (Data API checkCompatibility) — call this when ga4_run_report "
        "returns INVALID_ARGUMENT about incompatible fields. Returns per-field COMPATIBLE/INCOMPATIBLE plus the list of incompatible names. "
        "compatibility_filter 'INCOMPATIBLE' lists only the fields that cannot be added to the current selection."
    ))
    @audited(deps, "ga4_check_compatibility")
    async def ga4_check_compatibility(
        property_id: str,
        metrics: list[str] | None = None,
        dimensions: list[str] | None = None,
        dimension_filter: dict[str, Any] | None = None,
        metric_filter: dict[str, Any] | None = None,
        compatibility_filter: str | None = None,
    ) -> dict[str, Any]:
        prop = v.property_id(property_id)
        mets = v.api_names(metrics, label="metrics", minimum=0, maximum=10)
        dims = v.api_names(dimensions, label="dimensions", minimum=0, maximum=9)
        if not mets and not dims:
            raise ToolError("give at least one metric or dimension to check")
        body: dict[str, Any] = {"metrics": [{"name": m} for m in mets], "dimensions": [{"name": d} for d in dims]}
        if (f := normalize_filter(dimension_filter, kind="dimension")):
            body["dimensionFilter"] = f
        if (f := normalize_filter(metric_filter, kind="metric")):
            body["metricFilter"] = f
        if compatibility_filter:
            cf = str(compatibility_filter).upper()
            if cf not in ("COMPATIBLE", "INCOMPATIBLE"):
                raise ToolError("compatibility_filter must be 'COMPATIBLE' or 'INCOMPATIBLE'")
            body["compatibilityFilter"] = cf
        resp = await client.data_post(prop, "checkCompatibility", body)
        result = compact_compatibility(resp)
        result["property"] = f"properties/{prop}"
        return result
