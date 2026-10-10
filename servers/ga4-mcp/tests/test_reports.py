"""Data API tools: request building, compaction, validation, error mapping, retries."""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import DATA, FAKE_TOKEN, audit_lines, call, report_response

pytestmark = pytest.mark.anyio

PROP = "320448620"
RUN_REPORT = f"{DATA}/v1beta/properties/{PROP}:runReport"


async def test_run_report_defaults_and_compaction(client, api, config_dir):
    route = api.post(RUN_REPORT).respond(
        json=report_response(
            [(["20261001"], ["120", "95.5"]), (["20261002"], ["80", "60"])],
            dims=["date"], mets=["sessions", "totalRevenue"], row_count=7,
            totals=[{"dimensionValues": [{"value": "RESERVED_TOTAL"}], "metricValues": [{"value": "200"}, {"value": "155.5"}]}],
        )
    )
    err, out = await call(client, "ga4_run_report", {"property_id": "properties/320448620", "metrics": ["sessions", "totalRevenue"], "dimensions": ["date"], "limit": 2})
    assert not err, out
    sent = json.loads(route.calls.last.request.content)
    assert sent["dateRanges"] == [{"startDate": "7daysAgo", "endDate": "yesterday"}]
    assert sent["limit"] == 2 and sent["offset"] == 0 and sent["keepEmptyRows"] is False
    assert route.calls.last.request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert out["property"] == "properties/320448620"
    assert out["rows"] == [{"date": "20261001", "sessions": 120, "totalRevenue": 95.5}, {"date": "20261002", "sessions": 80, "totalRevenue": 60}]
    assert out["row_count"] == 7 and out["returned_rows"] == 2 and out["next_offset"] == 2
    assert out["totals"] == [{"sessions": 200, "totalRevenue": 155.5}]
    assert out["metadata"] == {"currency_code": "BDT", "time_zone": "Asia/Dhaka"}
    assert out["truncated"] is False
    # audit line written, without the token or payload
    lines = audit_lines(config_dir)
    assert lines and lines[-1]["tool"] == "ga4_run_report" and lines[-1]["status"] == "ok"
    assert FAKE_TOKEN not in json.dumps(lines)
    assert lines[-1]["meta"]["returned_rows"] == 2


async def test_run_report_filter_shortcuts_and_order_by(client, api):
    route = api.post(RUN_REPORT).respond(json=report_response([], dims=["country"], mets=["sessions"]))
    err, out = await call(client, "ga4_run_report", {
        "property_id": PROP, "metrics": ["sessions"], "dimensions": ["country", "pagePath"],
        "dimension_filter": {"country": "Bangladesh", "pagePath": "~blog", "deviceCategory": ["mobile", "tablet"]},
        "metric_filter": {"sessions": ">100"},
        "order_by": "-sessions", "start_date": "2026-09-01", "end_date": "20260930",
    })
    assert not err, out
    sent = json.loads(route.calls.last.request.content)
    exprs = sent["dimensionFilter"]["andGroup"]["expressions"]
    assert exprs[0] == {"filter": {"fieldName": "country", "stringFilter": {"matchType": "EXACT", "value": "Bangladesh", "caseSensitive": False}}}
    assert exprs[1]["filter"]["stringFilter"]["matchType"] == "CONTAINS" and exprs[1]["filter"]["stringFilter"]["value"] == "blog"
    assert exprs[2]["filter"]["inListFilter"]["values"] == ["mobile", "tablet"]
    assert sent["metricFilter"] == {"filter": {"fieldName": "sessions", "numericFilter": {"operation": "GREATER_THAN", "value": {"int64Value": "100"}}}}
    assert sent["orderBys"] == [{"metric": {"metricName": "sessions"}, "desc": True}]
    assert sent["dateRanges"] == [{"startDate": "2026-09-01", "endDate": "2026-09-30"}]
    assert out["returned_rows"] == 0 and "note" in out


async def test_run_report_passes_full_filter_expression_through(client, api):
    route = api.post(RUN_REPORT).respond(json=report_response([], dims=[], mets=["sessions"]))
    expr = {"notExpression": {"filter": {"fieldName": "country", "stringFilter": {"value": "x"}}}}
    err, _ = await call(client, "ga4_run_report", {"property_id": PROP, "metrics": ["sessions"], "dimension_filter": expr})
    assert not err
    assert json.loads(route.calls.last.request.content)["dimensionFilter"] == expr


@pytest.mark.parametrize(
    "args, fragment",
    [
        ({"property_id": "abc", "metrics": ["sessions"]}, "property_id must be numeric"),
        ({"property_id": PROP, "metrics": []}, "at least 1"),
        ({"property_id": PROP, "metrics": ["sessions"], "start_date": "last week"}, "start_date must be"),
        ({"property_id": PROP, "metrics": ["sessions"], "limit": 5000}, "limit must be between"),
        ({"property_id": PROP, "metrics": ["sessions"], "order_by": "-users"}, "order_by 'users' must be one of"),
        ({"property_id": PROP, "metrics": ["bad name!"]}, "metrics must be a GA4 API name"),
        ({"property_id": PROP, "metrics": ["sessions"], "metric_filter": {"sessions": ">lots"}}, "needs a number"),
    ],
)
async def test_run_report_validation(client, api, args, fragment):
    err, text = await call(client, "ga4_run_report", args)
    assert err and fragment in text, text
    assert not api.calls  # nothing reached the API


async def test_run_report_maps_400_to_tool_error(client, api, config_dir):
    api.post(RUN_REPORT).respond(
        status_code=400,
        json={"error": {"code": 400, "message": "Field conversions is not a valid metric.", "status": "INVALID_ARGUMENT"}},
    )
    err, text = await call(client, "ga4_run_report", {"property_id": PROP, "metrics": ["conversions"]})
    assert err
    assert "GA4 API 400 (INVALID_ARGUMENT): Field conversions is not a valid metric." in text
    assert "ga4_get_metadata" in text
    assert audit_lines(config_dir)[-1]["status"] == "error"


async def test_run_report_retries_on_429_then_succeeds(client, api):
    route = api.post(RUN_REPORT)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
        httpx.Response(200, json=report_response([([], ["5"])], dims=[], mets=["sessions"])),
    ]
    err, out = await call(client, "ga4_run_report", {"property_id": PROP, "metrics": ["sessions"]})
    assert not err, out
    assert route.call_count == 2
    assert out["rows"] == [{"sessions": 5}]


async def test_run_report_gives_up_after_retries(client, api):
    route = api.post(RUN_REPORT).respond(status_code=503, json={"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}})
    err, text = await call(client, "ga4_run_report", {"property_id": PROP, "metrics": ["sessions"]})
    assert err and "GA4 API 503" in text
    assert route.call_count == 4  # 1 + 3 retries


async def test_run_report_401_forces_one_refresh(client, api, token_provider):
    route = api.post(RUN_REPORT)
    route.side_effect = [
        httpx.Response(401, json={"error": {"code": 401, "message": "expired", "status": "UNAUTHENTICATED"}}),
        httpx.Response(200, json=report_response([], dims=[], mets=["sessions"])),
    ]
    err, _ = await call(client, "ga4_run_report", {"property_id": PROP, "metrics": ["sessions"]})
    assert not err
    assert token_provider.force_refreshes == 1


async def test_run_report_output_budget_truncates(client, api):
    rows = [([f"/page/{i}/" + "x" * 80], [str(i)]) for i in range(2000)]
    api.post(RUN_REPORT).respond(json=report_response(rows, dims=["pagePath"], mets=["sessions"], row_count=2000))
    err, out = await call(client, "ga4_run_report", {"property_id": PROP, "metrics": ["sessions"], "dimensions": ["pagePath"], "limit": 1000})
    assert not err
    assert out["truncated"] is True and out["returned_rows"] == 2000 and len(out["rows"]) < 2000
    assert "truncation_note" in out
    assert len(json.dumps(out, indent=2, ensure_ascii=False).encode()) <= 50_000


async def test_realtime_report(client, api):
    route = api.post(f"{DATA}/v1beta/properties/{PROP}:runRealtimeReport").respond(
        json=report_response([(["Bangladesh"], ["12"])], dims=["country"], mets=["activeUsers"])
    )
    err, out = await call(client, "ga4_run_realtime_report", {
        "property_id": PROP, "metrics": ["activeUsers"], "dimensions": ["country"],
        "minute_ranges": [{"name": "last5", "start_minutes_ago": 5, "end_minutes_ago": 0}],
    })
    assert not err, out
    sent = json.loads(route.calls.last.request.content)
    assert sent["minuteRanges"] == [{"name": "last5", "startMinutesAgo": 5, "endMinutesAgo": 0}]
    assert "dateRanges" not in sent
    assert out["rows"] == [{"country": "Bangladesh", "activeUsers": 12}]
    assert out["window"].startswith("realtime")


async def test_pivot_report(client, api):
    resp = report_response([(["20261001", "mobile"], ["10"])], dims=["date", "deviceCategory"], mets=["sessions"])
    resp.pop("rowCount")
    resp["pivotHeaders"] = [
        {"pivotDimensionHeaders": [{"dimensionValues": [{"value": "20261001"}]}], "rowCount": 1},
        {"pivotDimensionHeaders": [{"dimensionValues": [{"value": "mobile"}]}], "rowCount": 1},
    ]
    route = api.post(f"{DATA}/v1beta/properties/{PROP}:runPivotReport").respond(json=resp)
    err, out = await call(client, "ga4_run_pivot_report", {
        "property_id": PROP, "metrics": ["sessions"], "dimensions": ["date", "deviceCategory"],
        "pivots": [{"field_names": ["date"], "limit": 31, "order_by": "-sessions"}, {"field_names": ["deviceCategory"], "limit": 5}],
    })
    assert not err, out
    sent = json.loads(route.calls.last.request.content)
    assert sent["pivots"][0] == {"fieldNames": ["date"], "limit": 31, "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}]}
    assert out["pivot_headers"][1]["values"] == [["mobile"]]
    assert out["rows"] == [{"date": "20261001", "deviceCategory": "mobile", "sessions": 10}]


async def test_pivot_requires_dimension_membership(client, api):
    err, text = await call(client, "ga4_run_pivot_report", {
        "property_id": PROP, "metrics": ["sessions"], "dimensions": ["date"], "pivots": [{"field_names": ["country"]}],
    })
    assert err and "must be in dimensions" in text


async def test_batch_run_reports(client, api):
    route = api.post(f"{DATA}/v1beta/properties/{PROP}:batchRunReports").respond(json={
        "reports": [
            report_response([(["Organic Search"], ["50"])], dims=["sessionDefaultChannelGroup"], mets=["sessions"]),
            report_response([(["/"], ["30"])], dims=["pagePath"], mets=["screenPageViews"]),
        ]
    })
    err, out = await call(client, "ga4_batch_run_reports", {"property_id": PROP, "requests": [
        {"name": "channels", "metrics": ["sessions"], "dimensions": ["sessionDefaultChannelGroup"]},
        {"metrics": ["screenPageViews"], "dimensions": ["pagePath"], "limit": 10},
    ]})
    assert not err, out
    sent = json.loads(route.calls.last.request.content)
    assert len(sent["requests"]) == 2 and sent["requests"][1]["limit"] == 10
    assert out["count"] == 2 and out["reports"][0]["name"] == "channels" and out["reports"][1]["name"] == "report_2"
    assert out["reports"][1]["rows"] == [{"pagePath": "/", "screenPageViews": 30}]


async def test_batch_rejects_unknown_keys_and_too_many(client, api):
    err, text = await call(client, "ga4_batch_run_reports", {"property_id": PROP, "requests": [{"metrics": ["sessions"], "foo": 1}]})
    assert err and "unknown keys" in text
    err, text = await call(client, "ga4_batch_run_reports", {"property_id": PROP, "requests": [{"metrics": ["sessions"]}] * 6})
    assert err and "at most 5" in text


async def test_get_metadata_search(client, api):
    api.get(f"{DATA}/v1beta/properties/{PROP}/metadata").respond(json={
        "name": f"properties/{PROP}/metadata",
        "dimensions": [
            {"apiName": "country", "uiName": "Country", "category": "Geography", "description": "Country of users"},
            {"apiName": "customEvent:plan", "uiName": "Plan", "category": "Custom", "customDefinition": True},
        ],
        "metrics": [
            {"apiName": "keyEvents", "uiName": "Key events", "category": "Event", "type": "TYPE_FLOAT", "deprecatedApiNames": ["conversions"]},
            {"apiName": "totalRevenue", "uiName": "Total revenue", "category": "Revenue", "type": "TYPE_CURRENCY"},
        ],
    })
    err, out = await call(client, "ga4_get_metadata", {"property_id": PROP, "search": "revenue"})
    assert not err, out
    assert out["dimension_count"] == 0 and out["metric_count"] == 1
    assert out["metrics"][0]["api_name"] == "totalRevenue" and out["metrics"][0]["type"] == "TYPE_CURRENCY"
    err, out = await call(client, "ga4_get_metadata", {"property_id": PROP, "custom_only": True})
    assert out["dimensions"] == [{"api_name": "customEvent:plan", "ui_name": "Plan", "category": "Custom", "custom": True}]
    err, out = await call(client, "ga4_get_metadata", {"property_id": PROP, "kind": "metrics"})
    assert out["metrics"][0]["deprecated_api_names"] == ["conversions"] and out["dimensions"] == []


async def test_check_compatibility(client, api):
    route = api.post(f"{DATA}/v1beta/properties/{PROP}:checkCompatibility").respond(json={
        "dimensionCompatibilities": [{"dimensionMetadata": {"apiName": "itemName"}, "compatibility": "INCOMPATIBLE"}],
        "metricCompatibilities": [{"metricMetadata": {"apiName": "sessions"}, "compatibility": "COMPATIBLE"}],
    })
    err, out = await call(client, "ga4_check_compatibility", {"property_id": PROP, "metrics": ["sessions"], "dimensions": ["itemName"], "compatibility_filter": "incompatible"})
    assert not err, out
    assert json.loads(route.calls.last.request.content)["compatibilityFilter"] == "INCOMPATIBLE"
    assert out["incompatible"] == ["itemName"] and out["all_compatible"] is False
