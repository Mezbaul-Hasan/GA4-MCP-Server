"""Admin API tools: routing (v1beta vs v1alpha), params, pagination, compaction, errors."""

from __future__ import annotations

import pytest

from conftest import ACCOUNT_SUMMARIES, ADMIN, KEY_EVENTS, PROPERTY, RETENTION, STREAMS, call

pytestmark = pytest.mark.anyio

PROP = "320448620"


async def test_list_account_summaries(client, api):
    route = api.get(f"{ADMIN}/v1beta/accountSummaries").respond(json={**ACCOUNT_SUMMARIES, "nextPageToken": "tok123"})
    err, out = await call(client, "ga4_list_account_summaries", {"page_size": 10})
    assert not err, out
    assert route.calls.last.request.url.params["pageSize"] == "10"
    assert out["count"] == 1 and out["accounts"][0]["account_id"] == "111"
    assert out["accounts"][0]["properties"][0] == {
        "property": "properties/320448620", "property_id": "320448620", "display_name": "Shop BD — GA4",
        "property_type": "PROPERTY_TYPE_ORDINARY", "parent": "accounts/111",
    }
    assert out["next_page_token"] == "tok123" and "more results" in out["note"]


async def test_page_token_is_forwarded_and_validated(client, api):
    route = api.get(f"{ADMIN}/v1beta/accountSummaries").respond(json={"accountSummaries": []})
    err, out = await call(client, "ga4_list_account_summaries", {"page_token": "tok123"})
    assert not err and route.calls.last.request.url.params["pageToken"] == "tok123"
    assert "next_page_token" not in out
    err, text = await call(client, "ga4_list_account_summaries", {"page_size": 0})
    assert err and "page_size must be between" in text
    err, text = await call(client, "ga4_list_account_summaries", {"page_token": "has space"})
    assert err and "page_token" in text


async def test_list_properties_uses_parent_filter(client, api):
    route = api.get(f"{ADMIN}/v1beta/properties").respond(json={"properties": [PROPERTY]})
    err, out = await call(client, "ga4_list_properties", {"account_id": "accounts/111", "show_deleted": True})
    assert not err, out
    params = route.calls.last.request.url.params
    assert params["filter"] == "parent:accounts/111" and params["showDeleted"] == "true"
    assert out["account"] == "accounts/111"
    assert out["properties"][0]["time_zone"] == "Asia/Dhaka" and out["properties"][0]["property_id"] == PROP


async def test_get_property_and_account(client, api):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}").respond(json=PROPERTY)
    api.get(f"{ADMIN}/v1beta/accounts/111").respond(json={"name": "accounts/111", "displayName": "Agency Account", "regionCode": "BD"})
    err, out = await call(client, "ga4_get_property", {"property_id": PROP})
    assert not err and out["currency_code"] == "BDT" and out["service_level"] == "GOOGLE_ANALYTICS_STANDARD"
    err, out = await call(client, "ga4_get_account", {"account_id": "111"})
    assert not err and out == {"name": "accounts/111", "account_id": "111", "display_name": "Agency Account", "region_code": "BD"}


async def test_streams_key_events_retention(client, api):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataStreams").respond(json=STREAMS)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataStreams/987").respond(json=STREAMS["dataStreams"][0])
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/keyEvents").respond(json=KEY_EVENTS)
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/dataRetentionSettings").respond(json=RETENTION)

    err, out = await call(client, "ga4_list_data_streams", {"property_id": PROP})
    assert not err and out["data_streams"][0]["measurement_id"] == "G-ABC123" and out["data_streams"][0]["stream_id"] == "987"
    err, out = await call(client, "ga4_get_data_stream", {"property_id": PROP, "stream_id": "properties/320448620/dataStreams/987"})
    assert not err and out["default_uri"] == "https://shop.example"
    err, out = await call(client, "ga4_list_key_events", {"property_id": PROP})
    assert not err and out["key_events"][0]["event_name"] == "purchase" and out["key_events"][0]["custom"] is False
    err, out = await call(client, "ga4_get_data_retention_settings", {"property_id": PROP})
    assert not err and out["event_data_retention"] == "TWO_MONTHS"


async def test_custom_definitions_report_names(client, api):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/customDimensions").respond(json={"customDimensions": [
        {"name": f"properties/{PROP}/customDimensions/1", "parameterName": "plan", "displayName": "Plan", "scope": "USER"},
    ]})
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/customMetrics").respond(json={"customMetrics": [
        {"name": f"properties/{PROP}/customMetrics/1", "parameterName": "score", "displayName": "Score", "scope": "EVENT", "measurementUnit": "STANDARD"},
    ]})
    err, out = await call(client, "ga4_list_custom_dimensions", {"property_id": PROP})
    assert not err and out["custom_dimensions"][0]["report_name"] == "customUser:plan"
    err, out = await call(client, "ga4_list_custom_metrics", {"property_id": PROP})
    assert not err and out["custom_metrics"][0]["report_name"] == "customEvent:score"


async def test_alpha_resources_use_v1alpha(client, api):
    api.get(f"{ADMIN}/v1alpha/properties/{PROP}/audiences").respond(json={"audiences": [{"name": f"properties/{PROP}/audiences/1", "displayName": "All Users", "membershipDurationDays": 30}]})
    api.get(f"{ADMIN}/v1alpha/properties/{PROP}/bigQueryLinks").respond(json={"bigqueryLinks": [{"name": f"properties/{PROP}/bigQueryLinks/1", "project": "projects/p", "dailyExportEnabled": True}]})
    api.get(f"{ADMIN}/v1alpha/properties/{PROP}/attributionSettings").respond(json={"name": f"properties/{PROP}/attributionSettings", "reportingAttributionModel": "PAID_AND_ORGANIC_CHANNELS_DATA_DRIVEN"})
    api.get(f"{ADMIN}/v1beta/properties/{PROP}/googleAdsLinks").respond(json={"googleAdsLinks": [{"name": f"properties/{PROP}/googleAdsLinks/1", "customerId": "1234567890", "adsPersonalizationEnabled": True}]})

    err, out = await call(client, "ga4_list_audiences", {"property_id": PROP})
    assert not err and out["audiences"][0]["display_name"] == "All Users" and "v1alpha" in out["api_version"]
    err, out = await call(client, "ga4_list_bigquery_links", {"property_id": PROP})
    assert not err and out["bigquery_links"][0]["daily_export_enabled"] is True
    err, out = await call(client, "ga4_get_attribution_settings", {"property_id": PROP})
    assert not err and out["reporting_attribution_model"].endswith("DATA_DRIVEN")
    err, out = await call(client, "ga4_list_google_ads_links", {"property_id": PROP})
    assert not err and out["google_ads_links"][0]["customer_id"] == "1234567890"


@pytest.mark.parametrize(
    "status, google_status, fragment",
    [
        (403, "PERMISSION_DENIED", "lacks access to the property"),
        (404, "NOT_FOUND", "ga4_quick_overview"),
        (401, "UNAUTHENTICATED", "ga4-mcp auth"),
    ],
)
async def test_admin_error_mapping(client, api, status, google_status, fragment):
    api.get(f"{ADMIN}/v1beta/properties/{PROP}").respond(status_code=status, json={"error": {"code": status, "message": "nope", "status": google_status}})
    err, text = await call(client, "ga4_get_property", {"property_id": PROP})
    assert err and f"GA4 API {status} ({google_status}): nope" in text and fragment in text


async def test_invalid_ids_never_reach_api(client, api):
    for tool, args in [
        ("ga4_get_property", {"property_id": "properties/abc"}),
        ("ga4_get_account", {"account_id": "x"}),
        ("ga4_get_data_stream", {"property_id": PROP, "stream_id": "../../accounts"}),
        ("ga4_list_properties", {"account_id": "accounts/1 OR 1"}),
    ]:
        err, text = await call(client, tool, args)
        assert err and "must be numeric" in text, (tool, text)
    assert not api.calls
