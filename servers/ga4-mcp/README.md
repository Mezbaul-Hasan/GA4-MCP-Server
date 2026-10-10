# ga4-mcp — Google Analytics 4 MCP server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude Desktop, Cursor, VS Code or any
MCP client **read Google Analytics 4** — reports (Data API v1beta) and configuration (Admin API v1beta/v1alpha).

- Runs **on your own computer** over stdio. You sign in **with your own Google account** in the browser once;
  the refresh token stays in `~/.ga4-mcp/config.json` (file mode 0600). Nothing is hosted anywhere.
- **Phase 1 = read-only.** All 25 tools carry `readOnlyHint=true`; the only Google scope requested is
  `https://www.googleapis.com/auth/analytics.readonly`. You can only see what your Google account can see.
- Compact outputs (≤ 50 KB, rows as `{dimension: value, metric: number}`), pagination, retry/backoff on
  429/5xx, validated inputs, clear error messages, local audit log.

## 1. One-time setup (≈10 minutes)

### 1.1 Google Cloud OAuth client (owner does this once for the team, or each person for themselves)
1. Open [Google Cloud Console](https://console.cloud.google.com/) → create/select a project.
2. **APIs & Services → Library**: enable **Google Analytics Data API** and **Google Analytics Admin API**.
3. **APIs & Services → OAuth consent screen**: User type *External* (or *Internal* if you use Google Workspace),
   fill app name + support e-mail, add the scope `…/auth/analytics.readonly`.
   ⚠️ While the publishing status is **Testing**, Google issues refresh tokens that **expire after 7 days** and only
   listed *test users* can sign in. Click **Publish app** (status *In production*) to get long-lived tokens — Google
   may show an "unverified app" warning for an internal tool; click *Advanced → Go to …* to continue.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → Application type **Desktop app**.
   Copy the **Client ID** and **Client secret**.

### 1.2 Install `uv` (once per computer)
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 1.3 Sign in
```bash
uvx --from "git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server#subdirectory=servers/ga4-mcp" ga4-mcp auth
```
Paste the client id + secret when asked (or set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`), choose your Google
account in the browser, click *Allow*. The command verifies the token and prints where it was saved.
Other commands: `ga4-mcp status`, `ga4-mcp logout`, `ga4-mcp help`.

### 1.4 Add the server to your MCP client
Claude Desktop → *Settings → Developer → Edit Config* (`claude_desktop_config.json`); Cursor → `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "ga4": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server#subdirectory=servers/ga4-mcp", "ga4-mcp"]
    }
  }
}
```
Restart the client and ask: *"Run ga4_quick_overview"* → you should see your GA4 accounts and properties.

Local development instead of `uvx`:
```bash
pip install -e common -e servers/ga4-mcp        # from the repo root
ga4-mcp auth
# Claude Desktop: {"command": "ga4-mcp"}   or   {"command": "python", "args": ["/path/to/servers/ga4-mcp/server.py"]}
mcp dev servers/ga4-mcp/server.py               # MCP Inspector
```

## 2. Tools (all 🟢 read-only)

| Tool | What it does |
|---|---|
| **Reports (Data API)** | |
| `ga4_run_report` | Main report tool: metrics × dimensions × date range, filters (`{'country':'Bangladesh'}`, `{'pagePath':'~blog'}`, `{'sessions':'>100'}`), `order_by '-sessions'`, `limit`/`offset`, comparison `date_ranges`, totals |
| `ga4_run_realtime_report` | Users/events in the last 30 minutes (optional `minute_ranges`) |
| `ga4_run_pivot_report` | Cross-tab (e.g. date × deviceCategory) |
| `ga4_batch_run_reports` | Up to 5 reports in one call |
| `ga4_get_metadata` | Valid dimension/metric names for a property (`search`, `custom_only`) |
| `ga4_check_compatibility` | Which fields can be combined in one report |
| **Configuration (Admin API)** | |
| `ga4_list_account_summaries` | All accounts → properties you can access (start here) |
| `ga4_list_accounts`, `ga4_get_account` | Accounts |
| `ga4_list_properties`, `ga4_get_property` | Properties of an account; time zone, currency, service level |
| `ga4_get_data_retention_settings` | Event/user data retention |
| `ga4_list_data_streams`, `ga4_get_data_stream` | Web/app streams, measurement id (G-XXXX) |
| `ga4_list_key_events` | Key events (conversions) |
| `ga4_list_custom_dimensions`, `ga4_list_custom_metrics` | Custom definitions with their report names |
| `ga4_list_google_ads_links` | Linked Google Ads accounts |
| `ga4_list_bigquery_links` *(v1alpha)* | BigQuery export links |
| `ga4_list_audiences` *(v1alpha)* | Audiences |
| `ga4_get_attribution_settings` *(v1alpha)* | Attribution model and lookback windows |
| **Helpers** | |
| `ga4_quick_overview` | Everything you can access, in one call |
| `ga4_find_property` | Find a property by name/keyword/id |
| `ga4_property_health_check` | Settings + streams + key events + yesterday vs 7-day average + realtime + warnings |
| `ga4_raw_get` | GET any read endpoint of the two Google Analytics APIs (host allow-listed; GET only) |

Things the model is told (server `instructions`): reports use the **property's time zone**; the metric is
**`keyEvents`** (GA4 renamed *conversions*); `date` comes back as `YYYYMMDD`; default range = last 7 days ending yesterday.

## 3. Configuration
No environment variables are required. Optional ones are documented in [`.env.example`](.env.example):
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` (override the config file),
`GA4_MCP_CONFIG_DIR` (default `~/.ga4-mcp`), `GA4_MCP_AUDIT_DISABLED`, `MCP_LOG_LEVEL`.

Files on your machine: `~/.ga4-mcp/config.json` (credentials, 0600) and `~/.ga4-mcp/audit.jsonl`
(one line per tool call: time, tool, redacted arguments, duration, status — never results or tokens).

## 4. Security notes
- Scope is read-only; the server has no write tool, and `ga4_raw_get` refuses non-GET methods and any host other
  than `analyticsadmin.googleapis.com` / `analyticsdata.googleapis.com` (no token exfiltration path).
- Logs go to stderr only and pass through a redaction filter (access/refresh tokens, client secrets).
- Numeric ids are validated before they are placed in URLs; outputs are capped at 50 KB.
- Revoke access any time: `ga4-mcp logout` or https://myaccount.google.com/permissions.

## 5. Development
```bash
pip install -e common[dev] -e servers/ga4-mcp[dev]
(cd common && pytest -q)            # shared helpers incl. a full loopback-OAuth simulation
(cd servers/ga4-mcp && pytest -q)   # 25 tools via in-process MCP client, Google APIs mocked with respx
```
No test touches the network. Platform facts and sources: [`docs/platform-notes/ga4.md`](../../docs/platform-notes/ga4.md).

## 6. Troubleshooting
| Message | Fix |
|---|---|
| `not signed in — run ga4-mcp auth` | Run `ga4-mcp auth` in a terminal (same user account as the MCP client) |
| `invalid_grant` every ~7 days | Consent screen is in *Testing* → publish it (section 1.1 step 3) |
| `GA4 API 403 (PERMISSION_DENIED)` | Your Google account lacks Viewer access to that property, or the two APIs are not enabled in the Cloud project of the OAuth client |
| `GA4 API 400 (INVALID_ARGUMENT)` | Wrong dimension/metric name or incompatible combination → `ga4_get_metadata`, `ga4_check_compatibility` |
| `GA4 API 429 (RESOURCE_EXHAUSTED)` | Property quota (tokens/hour, 10 concurrent) — wait, reduce rows/columns, `return_property_quota=true` shows the counters |
| Browser did not open | Copy the printed URL into any browser on the same computer |
