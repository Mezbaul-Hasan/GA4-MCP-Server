# Platform notes — Google Analytics 4 (verified 2026-10-10)

Scope of `ga4-mcp` Phase 1: **read-only** tools on the Data API (reports) and Admin API (configuration). Writes are Phase 2.

## APIs and versions
| API | Base URL | Version we use | Notes |
|---|---|---|---|
| **Data API** (reports) | `https://analyticsdata.googleapis.com` | `v1beta` | Core methods: `runReport`, `runPivotReport`, `batchRunReports`, `batchRunPivotReports`, `getMetadata`, `checkCompatibility`; Realtime: `runRealtimeReport` (separate quota) — see [quota categories](https://developers.google.com/analytics/devguides/reporting/data/v1/quotas) |
| **Admin API** (config) | `https://analyticsadmin.googleapis.com` | `v1beta` for stable resources, `v1alpha` only where a read exists nowhere else | v1beta resources: `accountSummaries`, `accounts`, `properties` (incl. `getDataRetentionSettings`, `runAccessReport`), `keyEvents`, `customDimensions`, `customMetrics`, `dataStreams`, `googleAdsLinks`, `firebaseLinks`; `conversionEvents` is **deprecated → use `keyEvents`** ([Admin API REST reference](https://developers.google.com/analytics/devguides/config/admin/v1/rest)) |
| Admin **v1alpha-only** resources | same | `v1alpha` | `audiences`, `bigQueryLinks`, `accessBindings` (users), `attributionSettings`, `channelGroups`, `calculatedMetrics`, `expandedDataSets`, `reportingDataAnnotations` … ([reference](https://developers.google.com/analytics/devguides/config/admin/v1/rest)). ⚠️ The reference repo (`was-ga4-mcp`) calls audiences / bigQueryLinks / accessBindings / attributionSettings under **`/v1beta/`** — those routes do not exist there. |

Discovery docs (machine-readable, useful for schema checks): `https://analyticsadmin.googleapis.com/$discovery/rest?version=v1beta` and `…?version=v1alpha`.

Google also ships an **official GA4 MCP server** (Python): https://github.com/googleanalytics/google-analytics-mcp — second reference for tool shapes (it is read-only and uses Application Default Credentials; ours uses a browser OAuth flow so non-developers can connect).

## Identifiers
- Property: numeric `properties/{PROPERTY_ID}` (e.g. `properties/320448620`). Tools accept `320448620` or `properties/320448620`.
- Account: `accounts/{ACCOUNT_ID}`. Data stream: `properties/{P}/dataStreams/{S}`; web streams carry `webStreamData.measurementId` (`G-XXXX`).
- Fastest discovery: `GET /v1beta/accountSummaries` (accounts → propertySummaries), paginated with `pageSize`/`pageToken`.

## OAuth (desktop / installed-app flow)
- Scopes — Phase 1 uses **only** `https://www.googleapis.com/auth/analytics.readonly` (valid for both Data and Admin API reads). Phase 2 adds `analytics.edit`; user management reads/writes need `analytics.manage.users(.readonly)` — not requested.
- Client type: **Desktop app** OAuth client; redirect to loopback `http://127.0.0.1:{random_port}/callback`; `access_type=offline` + `prompt=consent` to obtain a refresh token; we add **PKCE (S256)** and a random `state`.
- Token endpoint: `POST https://oauth2.googleapis.com/token` (`authorization_code`, then `refresh_token` grants). Access tokens last ~1 h; cache with a 5-minute margin.
- ⚠️ **7-day refresh tokens**: a project whose OAuth consent screen is *External* with publishing status **Testing** is issued refresh tokens that expire in 7 days ([Google OAuth docs, quoted](https://stackoverflow.com/questions/8953983/do-google-refresh-tokens-expire); [field report](https://dev.to/ko-hi/googles-oauth-testing-mode-expires-refresh-tokens-in-7-days-publish-the-consent-screen-before-24hm)). Fix: publish the consent screen to **In production** (for the analytics scopes Google may show an "unverified app" warning — acceptable for an internal tool), or use *Internal* user type on Google Workspace. The token JSON includes `refresh_token_expires_in` when the app is in Testing — our `auth` command warns if it sees it.
- Required APIs enabled in the Cloud project: `analyticsdata.googleapis.com`, `analyticsadmin.googleapis.com`.

## Quotas (Data API, standard property)
Core tokens per property: **200,000/day**, **40,000/hour**, **14,000/hour per project**; **10 concurrent requests**; 10 server errors per project per property per hour. Daily quotas reset at midnight PST. Add `"returnPropertyQuota": true` to any report request to get the live token status in the response ([quotas page](https://developers.google.com/analytics/devguides/reporting/data/v1/quotas)). Token cost grows with rows × columns × filter complexity × date range → our defaults: `limit=100`, last 7 days, no `keepEmptyRows`.

## Reporting semantics the model must know
- **`conversions` metric no longer exists — it is `keyEvents`** (per-event: `keyEvents:purchase`); dimension `isKeyEvent` ([source](https://docs.adverity.com/guides/tips-and-best-practices/google-analytics-data-ga4-tips.html), [rename background](https://www.nicelookingdata.com/blog/ga4-key-events-vs-conversions)).
- Dates in reports come back as `YYYYMMDD`; relative ranges accepted: `today`, `yesterday`, `NdaysAgo`, plus `YYYY-MM-DD`. Reports are in the **property's time zone** (`ga4_get_property` → `timeZone`, `currencyCode`).
- Dimension/metric compatibility is not guaranteed — call `checkCompatibility` or `getMetadata` when a report returns `INVALID_ARGUMENT`.
- Traffic-source dimensions have prefixes: `sessionSource/Medium/DefaultChannelGroup` (session scope), `firstUser…` (user scope); un-prefixed `source/medium` are key-event-scoped.
- Responses may be **thresholded** (rows withheld for privacy) — surfaced in `metadata.dataLossFromOtherRow` / `subjectToThresholding`; our tools pass these flags through.
- Realtime reports cover the last 30 minutes (up to 60 with `minuteRanges`), separate quota.

## Error → ToolError mapping (Admin & Data API share Google's error envelope)
| HTTP | `status` | Meaning / hint we return |
|---|---|---|
| 400 | `INVALID_ARGUMENT` | bad dimension/metric/filter → "check names with ga4_get_metadata / ga4_check_compatibility" |
| 401 | `UNAUTHENTICATED` | token expired/revoked → "run `ga4-mcp auth` again" |
| 403 | `PERMISSION_DENIED` | no access to property, API not enabled, or missing scope → say which |
| 404 | `NOT_FOUND` | wrong property/stream id |
| 429 | `RESOURCE_EXHAUSTED` | quota → retry with backoff (max 3), then report remaining quota if known |
| 5xx | — | retry with backoff; counts against the server-error quota, so cap retries |

## Phase 1 tool list (all 🟢 read-only)
Data: `ga4_run_report`, `ga4_run_realtime_report`, `ga4_run_pivot_report`, `ga4_batch_run_reports`, `ga4_get_metadata`, `ga4_check_compatibility`
Admin: `ga4_list_account_summaries`, `ga4_list_accounts`, `ga4_get_account`, `ga4_list_properties`, `ga4_get_property`, `ga4_list_data_streams`, `ga4_get_data_stream`, `ga4_list_key_events`, `ga4_list_custom_dimensions`, `ga4_list_custom_metrics`, `ga4_list_google_ads_links`, `ga4_get_data_retention_settings`, `ga4_list_audiences` (v1alpha), `ga4_list_bigquery_links` (v1alpha), `ga4_get_attribution_settings` (v1alpha)
Helpers: `ga4_quick_overview`, `ga4_find_property`, `ga4_property_health_check`, `ga4_raw_get` (GET-only, host allow-list `analyticsdata|analyticsadmin.googleapis.com`)

Deferred to Phase 2 (🟡/🔴 + `dry_run`, needs `analytics.edit`): update property, create key event, create/archive custom dimension & metric, create BigQuery link, data retention update. Not planned: user management (`accessBindings`), account/property delete.
