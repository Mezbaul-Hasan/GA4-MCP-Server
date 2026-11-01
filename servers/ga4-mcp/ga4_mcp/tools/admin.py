"""Admin API tools (configuration reads) — all read-only.

v1beta for stable resources; v1alpha only where no v1beta read exists (audiences,
BigQuery links, attribution settings) — those outputs carry an ``api_version`` note.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.pagination import enforce_output_budget, validate_page_size, validate_page_token

from .. import config, validation as v
from ._shared import READ_ONLY, Deps, audited, drop_empty

ALPHA_NOTE = "served by Admin API v1alpha (pre-release surface; field names may change)"


def _id_of(name: str | None) -> str | None:
    return name.rsplit("/", 1)[-1] if name else None


def compact_property(p: dict[str, Any]) -> dict[str, Any]:
    return drop_empty({
        "name": p.get("name"),
        "property_id": _id_of(p.get("name")),
        "display_name": p.get("displayName"),
        "property_type": p.get("propertyType"),
        "time_zone": p.get("timeZone"),
        "currency_code": p.get("currencyCode"),
        "industry_category": p.get("industryCategory"),
        "service_level": p.get("serviceLevel"),
        "parent": p.get("parent"),
        "account": p.get("account"),
        "create_time": p.get("createTime"),
        "update_time": p.get("updateTime"),
        "delete_time": p.get("deleteTime"),
        "expire_time": p.get("expireTime"),
    })


def compact_stream(s: dict[str, Any]) -> dict[str, Any]:
    web = s.get("webStreamData") or {}
    android = s.get("androidAppStreamData") or {}
    ios = s.get("iosAppStreamData") or {}
    return drop_empty({
        "name": s.get("name"),
        "stream_id": _id_of(s.get("name")),
        "type": s.get("type"),
        "display_name": s.get("displayName"),
        "measurement_id": web.get("measurementId"),
        "default_uri": web.get("defaultUri"),
        "firebase_app_id": web.get("firebaseAppId") or android.get("firebaseAppId") or ios.get("firebaseAppId"),
        "package_name": android.get("packageName"),
        "bundle_id": ios.get("bundleId"),
        "create_time": s.get("createTime"),
        "update_time": s.get("updateTime"),
    })


def _list_result(items: list[dict[str, Any]], key: str, *, next_page_token: str | None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {**extra, "count": len(items), key: items, "next_page_token": next_page_token}
    if next_page_token:
        result["note"] = f"more results available — call again with page_token='{next_page_token[:12]}…' (use the full next_page_token)"
    result = {k: val for k, val in result.items() if val is not None}
    return enforce_output_budget(result, key, budget=config.OUTPUT_BUDGET_BYTES)


def register(mcp: MCPServer, deps: Deps) -> None:  # noqa: C901 - many small tools
    client = deps.client

    def paging(page_size: int | None, page_token: str | None) -> tuple[int, str | None]:
        return (
            validate_page_size(page_size, default=config.DEFAULT_PAGE_SIZE, maximum=config.MAX_PAGE_SIZE),
            validate_page_token(page_token),
        )

    # ------------------------------------------------------------------ accounts
    @mcp.tool(name="ga4_list_account_summaries", annotations=READ_ONLY, description=(
        "Fastest way to see every GA4 account and property this Google account can access (Admin API accountSummaries). "
        "Returns accounts → properties with ids and display names. Start here when you do not know a property_id."
    ))
    @audited(deps, "ga4_list_account_summaries")
    async def ga4_list_account_summaries(page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list("accountSummaries", "accountSummaries", page_size=size, page_token=token)
        accounts = []
        for a in items:
            accounts.append(drop_empty({
                "account": a.get("account"),
                "account_id": _id_of(a.get("account")),
                "display_name": a.get("displayName"),
                "properties": [
                    drop_empty({
                        "property": p.get("property"),
                        "property_id": _id_of(p.get("property")),
                        "display_name": p.get("displayName"),
                        "property_type": p.get("propertyType"),
                        "parent": p.get("parent"),
                    })
                    for p in a.get("propertySummaries", []) or []
                ],
            }))
        return _list_result(accounts, "accounts", next_page_token=nxt)

    @mcp.tool(name="ga4_list_accounts", annotations=READ_ONLY, description=(
        "List GA4 accounts (Admin API accounts.list) with region and create time. show_deleted includes soft-deleted accounts."
    ))
    @audited(deps, "ga4_list_accounts")
    async def ga4_list_accounts(page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None, show_deleted: bool = False) -> dict[str, Any]:
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list("accounts", "accounts", page_size=size, page_token=token, params={"showDeleted": "true" if show_deleted else None})
        accounts = [drop_empty({
            "name": a.get("name"), "account_id": _id_of(a.get("name")), "display_name": a.get("displayName"),
            "region_code": a.get("regionCode"), "create_time": a.get("createTime"), "update_time": a.get("updateTime"),
            "deleted": a.get("deleted"), "gmp_organization": a.get("gmpOrganization"),
        }) for a in items]
        return _list_result(accounts, "accounts", next_page_token=nxt)

    @mcp.tool(name="ga4_get_account", annotations=READ_ONLY, description="Get one GA4 account by id ('123456' or 'accounts/123456').")
    @audited(deps, "ga4_get_account")
    async def ga4_get_account(account_id: str) -> dict[str, Any]:
        acc = v.account_id(account_id)
        a = await client.admin_get(f"accounts/{acc}")
        return drop_empty({
            "name": a.get("name"), "account_id": acc, "display_name": a.get("displayName"), "region_code": a.get("regionCode"),
            "create_time": a.get("createTime"), "update_time": a.get("updateTime"), "deleted": a.get("deleted"),
            "gmp_organization": a.get("gmpOrganization"),
        })

    # ------------------------------------------------------------------ properties
    @mcp.tool(name="ga4_list_properties", annotations=READ_ONLY, description=(
        "List GA4 properties under one account (Admin API properties.list, filter parent:accounts/<id>). "
        "Needs account_id ('123456' or 'accounts/123456') — get it from ga4_list_account_summaries. Returns time zone, currency, type, service level per property."
    ))
    @audited(deps, "ga4_list_properties")
    async def ga4_list_properties(account_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None, show_deleted: bool = False) -> dict[str, Any]:
        acc = v.account_id(account_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(
            "properties", "properties", page_size=size, page_token=token,
            params={"filter": f"parent:accounts/{acc}", "showDeleted": "true" if show_deleted else None},
        )
        return _list_result([compact_property(p) for p in items], "properties", next_page_token=nxt, account=f"accounts/{acc}")

    @mcp.tool(name="ga4_get_property", annotations=READ_ONLY, description=(
        "Get one GA4 property: display name, time zone (reports use it), currency, property type, service level (standard/360), create time."
    ))
    @audited(deps, "ga4_get_property")
    async def ga4_get_property(property_id: str) -> dict[str, Any]:
        prop = v.property_id(property_id)
        return compact_property(await client.admin_get(f"properties/{prop}"))

    @mcp.tool(name="ga4_get_data_retention_settings", annotations=READ_ONLY, description=(
        "Event/user data retention of a property (TWO_MONTHS, FOURTEEN_MONTHS, …) and reset-on-new-activity flag. "
        "Retention shorter than 14 months limits explorations — a common audit finding."
    ))
    @audited(deps, "ga4_get_data_retention_settings")
    async def ga4_get_data_retention_settings(property_id: str) -> dict[str, Any]:
        prop = v.property_id(property_id)
        r = await client.admin_get(f"properties/{prop}/dataRetentionSettings")
        return drop_empty({
            "name": r.get("name"), "property": f"properties/{prop}",
            "event_data_retention": r.get("eventDataRetention"), "user_data_retention": r.get("userDataRetention"),
            "reset_user_data_on_new_activity": r.get("resetUserDataOnNewActivity"),
        })

    # ------------------------------------------------------------------ data streams
    @mcp.tool(name="ga4_list_data_streams", annotations=READ_ONLY, description=(
        "List the data streams of a property (web / iOS / Android) with measurement id (G-XXXX), default URL, Firebase app id."
    ))
    @audited(deps, "ga4_list_data_streams")
    async def ga4_list_data_streams(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(f"properties/{prop}/dataStreams", "dataStreams", page_size=size, page_token=token)
        return _list_result([compact_stream(s) for s in items], "data_streams", next_page_token=nxt, property=f"properties/{prop}")

    @mcp.tool(name="ga4_get_data_stream", annotations=READ_ONLY, description="Get one data stream by stream_id (numeric) of a property.")
    @audited(deps, "ga4_get_data_stream")
    async def ga4_get_data_stream(property_id: str, stream_id: str) -> dict[str, Any]:
        prop = v.property_id(property_id)
        sid = v.stream_id(stream_id)
        return compact_stream(await client.admin_get(f"properties/{prop}/dataStreams/{sid}"))

    # ------------------------------------------------------------------ events & definitions
    @mcp.tool(name="ga4_list_key_events", annotations=READ_ONLY, description=(
        "List key events (formerly conversions) configured on a property: event name, counting method, default value, custom/deletable flags. "
        "Report on them with metric 'keyEvents' or 'keyEvents:<eventName>'."
    ))
    @audited(deps, "ga4_list_key_events")
    async def ga4_list_key_events(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(f"properties/{prop}/keyEvents", "keyEvents", page_size=size, page_token=token)
        events = [drop_empty({
            "name": e.get("name"), "event_name": e.get("eventName"), "counting_method": e.get("countingMethod"),
            "default_value": e.get("defaultValue"), "custom": e.get("custom"), "deletable": e.get("deletable"), "create_time": e.get("createTime"),
        }) for e in items]
        return _list_result(events, "key_events", next_page_token=nxt, property=f"properties/{prop}")

    @mcp.tool(name="ga4_list_custom_dimensions", annotations=READ_ONLY, description=(
        "List custom dimensions of a property (parameter name, display name, scope EVENT/USER/ITEM). "
        "In reports they are 'customEvent:<parameter>', 'customUser:<parameter>' or 'customItem:<parameter>'."
    ))
    @audited(deps, "ga4_list_custom_dimensions")
    async def ga4_list_custom_dimensions(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(f"properties/{prop}/customDimensions", "customDimensions", page_size=size, page_token=token)
        dims = [drop_empty({
            "name": d.get("name"), "parameter_name": d.get("parameterName"), "display_name": d.get("displayName"),
            "description": d.get("description"), "scope": d.get("scope"),
            "report_name": f"custom{str(d.get('scope', 'EVENT')).capitalize()}:{d.get('parameterName')}",
            "disallow_ads_personalization": d.get("disallowAdsPersonalization"),
        }) for d in items]
        return _list_result(dims, "custom_dimensions", next_page_token=nxt, property=f"properties/{prop}")

    @mcp.tool(name="ga4_list_custom_metrics", annotations=READ_ONLY, description=(
        "List custom metrics of a property (parameter name, unit, scope). In reports they are 'customEvent:<parameter>'."
    ))
    @audited(deps, "ga4_list_custom_metrics")
    async def ga4_list_custom_metrics(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(f"properties/{prop}/customMetrics", "customMetrics", page_size=size, page_token=token)
        mets = [drop_empty({
            "name": m.get("name"), "parameter_name": m.get("parameterName"), "display_name": m.get("displayName"),
            "description": m.get("description"), "measurement_unit": m.get("measurementUnit"), "scope": m.get("scope"),
            "report_name": f"customEvent:{m.get('parameterName')}", "restricted_metric_type": m.get("restrictedMetricType"),
        }) for m in items]
        return _list_result(mets, "custom_metrics", next_page_token=nxt, property=f"properties/{prop}")

    # ------------------------------------------------------------------ links
    @mcp.tool(name="ga4_list_google_ads_links", annotations=READ_ONLY, description=(
        "List Google Ads accounts linked to a property (customer id, personalization flag, who created the link)."
    ))
    @audited(deps, "ga4_list_google_ads_links")
    async def ga4_list_google_ads_links(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(f"properties/{prop}/googleAdsLinks", "googleAdsLinks", page_size=size, page_token=token)
        links = [drop_empty({
            "name": l.get("name"), "customer_id": l.get("customerId"), "can_manage_clients": l.get("canManageClients"),
            "ads_personalization_enabled": l.get("adsPersonalizationEnabled"), "creator_email_address": l.get("creatorEmailAddress"),
            "create_time": l.get("createTime"), "update_time": l.get("updateTime"),
        }) for l in items]
        return _list_result(links, "google_ads_links", next_page_token=nxt, property=f"properties/{prop}")

    @mcp.tool(name="ga4_list_bigquery_links", annotations=READ_ONLY, description=(
        "List BigQuery export links of a property (project, daily/streaming export flags, dataset location). Admin API v1alpha."
    ))
    @audited(deps, "ga4_list_bigquery_links")
    async def ga4_list_bigquery_links(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(
            f"properties/{prop}/bigQueryLinks", "bigqueryLinks", page_size=size, page_token=token, version=config.ADMIN_API_ALPHA_VERSION
        )
        links = [drop_empty({
            "name": l.get("name"), "project": l.get("project"), "dataset_location": l.get("datasetLocation"),
            "daily_export_enabled": l.get("dailyExportEnabled"), "streaming_export_enabled": l.get("streamingExportEnabled"),
            "fresh_daily_export_enabled": l.get("freshDailyExportEnabled"), "include_advertising_id": l.get("includeAdvertisingId"),
            "export_streams": l.get("exportStreams"), "excluded_events": l.get("excludedEvents"), "create_time": l.get("createTime"),
        }) for l in items]
        return _list_result(links, "bigquery_links", next_page_token=nxt, property=f"properties/{prop}", api_version=ALPHA_NOTE)

    # ------------------------------------------------------------------ audiences & attribution (alpha)
    @mcp.tool(name="ga4_list_audiences", annotations=READ_ONLY, description=(
        "List audiences defined on a property (name, membership duration, ads personalization, archived flag). Admin API v1alpha."
    ))
    @audited(deps, "ga4_list_audiences")
    async def ga4_list_audiences(property_id: str, page_size: int = config.DEFAULT_PAGE_SIZE, page_token: str | None = None) -> dict[str, Any]:
        prop = v.property_id(property_id)
        size, token = paging(page_size, page_token)
        items, nxt = await client.admin_list(
            f"properties/{prop}/audiences", "audiences", page_size=size, page_token=token, version=config.ADMIN_API_ALPHA_VERSION
        )
        audiences = [drop_empty({
            "name": a.get("name"), "display_name": a.get("displayName"), "description": a.get("description"),
            "membership_duration_days": a.get("membershipDurationDays"), "ads_personalization_enabled": a.get("adsPersonalizationEnabled"),
            "exclusion_duration_mode": a.get("exclusionDurationMode"), "archived": a.get("archived"), "create_time": a.get("createTime"),
            "event_trigger": a.get("eventTrigger"),
        }) for a in items]
        return _list_result(audiences, "audiences", next_page_token=nxt, property=f"properties/{prop}", api_version=ALPHA_NOTE)

    @mcp.tool(name="ga4_get_attribution_settings", annotations=READ_ONLY, description=(
        "Attribution settings of a property: reporting attribution model, acquisition/other key-event lookback windows, Ads export scope. Admin API v1alpha."
    ))
    @audited(deps, "ga4_get_attribution_settings")
    async def ga4_get_attribution_settings(property_id: str) -> dict[str, Any]:
        prop = v.property_id(property_id)
        r = await client.admin_get(f"properties/{prop}/attributionSettings", version=config.ADMIN_API_ALPHA_VERSION)
        return drop_empty({
            "name": r.get("name"), "property": f"properties/{prop}",
            "reporting_attribution_model": r.get("reportingAttributionModel"),
            "acquisition_conversion_event_lookback_window": r.get("acquisitionConversionEventLookbackWindow"),
            "other_conversion_event_lookback_window": r.get("otherConversionEventLookbackWindow"),
            "ads_web_conversion_data_export_scope": r.get("adsWebConversionDataExportScope"),
            "api_version": ALPHA_NOTE,
        })

