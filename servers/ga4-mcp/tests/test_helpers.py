"""Helper tools: overview, search, health check, raw GET allow-list; plus server-level checks."""

from __future__ import annotations

import json

import httpx

import pytest

from conftest import ACCOUNT_SUMMARIES, ADMIN, DATA, KEY_EVENTS, PROPERTY, RETENTION, STREAMS, call, report_response

pytestmark = pytest.mark.anyio

PROP = "320448620"


async def test_all_tools_are_read_only_with_descriptions(client):
    tools = (await client.list_tools()).tools
    names = {t.name for t in tools}
    assert len(tools) == 25
    assert names >= {"ga4_run_report", "ga4_list_account_summaries", "ga4_quick_overview", "ga4_property_health_check", "ga4_raw_get"}
    for t in tools:
        assert t.name.startswith("ga4_"), t.name
        assert t.annotations and t.annotations.read_only_hint is True and t.annotations.destructive_hint is False, t.name
        assert t.description and len(t.description) > 40, t.name


async def test_quick_overview_and_find_property(client, api):
    api.get(f"{ADMIN}/v1beta/accountSummaries").respond(json=ACCOUNT_SUMMARIES)
    err, out = await call(client, "ga4_quick_overview")
    assert not err, out
    assert out["account_count"] == 1 and out["property_count"] == 2
    assert out["accounts"][0]["properties"][1]["display_name"] == "Blog"

    err, out = await call(client, "ga4_find_property", {"query": "shop"})
    assert not err and out["match_count"] == 1 and out["matches"][0]["property_id"] == PROP
    err, out = await call(client, "ga4_find_property", {"query": "3204"})
    assert out["match_count"] == 1
    err, out = await call(client, "ga4_find_property", {"query": "nothing-here"})
    assert out["match_count"] == 0 and "note" in out
    err, text = await call(client, "ga4_find_property", {"query": "  "})
    assert err and "query must be" in text


async def test_quick_overview_follows_pagination(client, api):
    route = api.get(f"{ADMIN}/v1beta/accountSummaries")
    route.side_effect = [
        httpx.Response(200, json={**ACCOUNT_SUMMARIES, "nextPageToken": "p2"}),
        httpx.Response(200, json={"accountSummaries": [{"account": "accounts/222", "displayName": "Second", "propertySummaries": [{"property": "properties/999", "displayName": "Nine"}]}]}),
    ]
    err, out = await call(client, "ga4_quick_overview")
    assert not err and out["account_count"] == 2 and out["property_count"] == 3
    assert route.calls[1].request.url.params["pageToken"] == "p2"


async def test_health_check_collects_warnings(client, api):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}").respond(json=PROPERTY)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataStreams").respond(json=STREAMS)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/keyEvents").respond(json=KEY_EVENTS)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataRetentionSettings").respond(json=RETENTION)
    mets = ["activeUsers", "sessions", "screenPageViews", "eventCount", "keyEvents"]
    run = api.post(f"{DATA}/v1beta/properties/{PROP}:runReport")
    run.side_effect = [
        httpx.Response(200, json=report_response([([], ["0", "0", "0", "0", "0"])], dims=[], mets=mets)),  # yesterday: nothing
        httpx.Response(200, json=report_response([([], ["700", "840", "2000", "9000", "35"])], dims=[], mets=mets)),  # previous 7 days
    ]
    api.post(f"{DATA}/v1beta/properties/{PROP}:runRealtimeReport").respond(json=report_response([([], ["3"])], dims=[], mets=["activeUsers"]))

    err, out = await call(client, "ga4_property_health_check", {"property_id": PROP})
    assert not err, out
    assert out["status"] == "problems_found"
    assert out["active_users_now"] == 3
    assert out["previous_7_days_daily_average"]["sessions"] == 120.0
    assert out["key_events"] == ["purchase"] and out["data_streams"][0]["measurement_id"] == "G-ABC123"
    joined = " | ".join(out["warnings"])
    assert "no sessions/users recorded yesterday" in joined
    assert "retention is 2 months" in joined
    assert "errors" not in out
    sent = [json.loads(c.request.content) for c in run.calls]
    assert sent[0]["dateRanges"] == [{"startDate": "yesterday", "endDate": "yesterday"}]
    assert sent[1]["dateRanges"] == [{"startDate": "8daysAgo", "endDate": "2daysAgo"}]


async def test_health_check_survives_partial_failures(client, api):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}").respond(json=PROPERTY)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataStreams").respond(json=STREAMS)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/keyEvents").respond(json={"keyEvents": []})
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataRetentionSettings").respond(status_code=403, json={"error": {"code": 403, "message": "no", "status": "PERMISSION_DENIED"}})
    mets = ["activeUsers", "sessions", "screenPageViews", "eventCount", "keyEvents"]
    api.post(f"{DATA}/v1beta/properties/{PROP}:runReport").respond(json=report_response([([], ["10", "12", "30", "100", "0"])], dims=[], mets=mets))
    api.post(f"{DATA}/v1beta/properties/{PROP}:runRealtimeReport").respond(status_code=429, json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    err, out = await call(client, "ga4_property_health_check", {"property_id": PROP})
    assert not err, out
    assert "retention" in out["errors"] and "realtime" in out["errors"]
    assert any("no key events configured" in w for w in out["warnings"])
    assert out["yesterday"]["sessions"] == 12


async def test_raw_get_allow_list(client, api):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/firebaseLinks").respond(json={"firebaseLinks": [{"name": "x", "project": "projects/abc"}]})
    err, out = await call(client, "ga4_raw_get", {"path_or_url": "v1beta/properties/320448620/firebaseLinks", "params": {"pageSize": 5}})
    assert not err, out
    assert out["data"]["firebaseLinks"][0]["project"] == "projects/abc"
    assert api.calls.last.request.url.params["pageSize"] == "5"

    err, text = await call(client, "ga4_raw_get", {"path_or_url": "https://evil.example.com/v1beta/accountSummaries"})
    assert err and "not allowed" in text
    err, text = await call(client, "ga4_raw_get", {"path_or_url": "http://analyticsadmin.googleapis.com/v1beta/accountSummaries"})
    assert err and "not allowed" in text or "https" in text
    err, text = await call(client, "ga4_raw_get", {"path_or_url": "v2/whatever"})
    assert err and "must start with" in text
    err, text = await call(client, "ga4_raw_get", {"path_or_url": "v1beta/../../token"})
    assert err and "must not contain" in text
    assert api.calls.call_count == 1  # only the first, legitimate request went out


async def test_not_signed_in_gives_clear_message(config_dir, api):
    """A real token provider with no credentials must produce a next-step message, not a crash."""
    from mcp.client import Client

    from ga4_mcp import config
    from ga4_mcp.server import build_deps, create_server
    from marketing_mcp_common.google_auth import GoogleCredentialStore, GoogleTokenProvider

    store = GoogleCredentialStore(config_dir, env_prefix="GOOGLE")
    provider = GoogleTokenProvider(store, app_name=config.APP_NAME)
    server = create_server(build_deps(token_provider=provider, config_dir=config_dir, audit_enabled=False))
    async with Client(server) as c:
        err, text = await call(c, "ga4_list_account_summaries")
    assert err and "not signed in" in text and "ga4-mcp auth" in text
    assert not api.calls
