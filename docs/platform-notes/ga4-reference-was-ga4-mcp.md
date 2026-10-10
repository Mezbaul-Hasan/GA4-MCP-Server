# Reference review — `mnsmasum62786/was-ga4-mcp` (read A→Z on 2026-10-10)

Repo: https://github.com/mnsmasum62786/was-ga4-mcp · v1.0.4 (2026-07-28) · MIT · author Abdullah Al Masum (Web Analytics Solution)
Stack: Node.js ≥18, ESM JavaScript, single dependency `@modelcontextprotocol/sdk ^1.29` (TS SDK v1, low-level `Server`).
Size: 14 files, ~1,170 lines. Verified locally: `node cli.js help/status` work; MCP `tools/list` over stdio returns **31 tools**.

## What it is
A **local, single-user GA4 MCP server** launched by `npx -y github:mnsmasum62786/was-ga4-mcp` from Claude Desktop/Cursor (stdio).
BYO Google Cloud OAuth *Desktop* client; `auth` command runs a browser OAuth flow and stores
`client_id`, `client_secret`, `refresh_token` in `~/.was-ga4-mcp/config.json` (0600). Env vars override the file (multi-account).

## File map
| File | Role |
|---|---|
| `cli.js` | command router: `auth` · `logout` · `status` · `help` · (none) → start server |
| `config-paths.js` | config paths + **SCOPES** = `analytics.readonly`, `analytics.edit`, `analytics.manage.users` |
| `auth.js` | OAuth2 auth-code flow, loopback redirect on random 127.0.0.1 port, `state` check, `access_type=offline`, `prompt=consent`, 5-min timeout, cross-platform browser open |
| `google-client.js` | loads creds at import (exits if missing); access-token refresh with cache (5-min margin); `googleFetch` (Bearer, JSON, error with status/code/body); routes `:runReport`-style paths → `analyticsdata.googleapis.com`, else `analyticsadmin.googleapis.com`; `ga4Get/Post/Patch/Delete/Raw` |
| `server.js` | low-level SDK `Server`; `tools/list` + `tools/call` handlers; `ok()` truncates output at 60,000 chars; `bad()` → `isError` with message/status/code/body; logs to **stderr** only ✔ |
| `tools/registry.js` | concatenates data + admin + helper tools |
| `tools/data.js` (6) | `run_report` (universal, v1beta, default 7daysAgo→yesterday, limit 1000), `run_realtime_report`, `run_pivot_report`, `batch_run_reports` (≤5), `get_metadata`, `check_compatibility` |
| `tools/admin.js` (21) | account summaries/accounts/get_account, list/get/**update** property, data streams list/get, key events list/**create** (uses `keyEvents`), custom dimensions list/**create**, custom metrics list/**create**, audiences, Google Ads links, BigQuery links list/**create**, `list_users` (accessBindings), attribution settings, data retention |
| `tools/helpers.js` (4) | `universal_call` (any method + **any URL**), `quick_overview`, `find_property` (name search), `property_health_check` (parallel: yesterday vs 7daysAgo, key events, streams) |

## Strengths (copy these ideas)
1. Small, readable, zero extra deps (native `fetch`); correct stdio hygiene (stderr logging).
2. Solid desktop OAuth: loopback redirect + `state` + offline + consent; 0700 dir / 0600 file; env override per account.
3. Rich, model-facing description on `run_report` (example dimensions/metrics/filters) — the most important tool is the best documented.
4. Helper tools solve real UX problems: discover property IDs (`quick_overview`, `find_property`) and a one-call `health_check`.
5. Errors carry Google's status/code/body so the model can self-correct; output truncation protects context.
6. Student-friendly README; `npx github:` distribution; real Windows fixes in CHANGELOG (battle-tested).

## Gaps vs our PROJECT.md rules (do differently in `ga4-mcp`)
| # | Finding | Severity | Our rule |
|---|---|---|---|
| 1 | Always requests `analytics.edit` + `analytics.manage.users`, even for read-only use | 🟠 | §6 minimal scopes: `analytics.readonly` by default, edit opt-in |
| 2 | **No tool annotations**, no `dry_run`; 5 write tools execute immediately (`update_property`, `create_conversion_event`, `create_custom_dimension`, `create_custom_metric`, `create_bigquery_link`). Custom dimensions cannot be deleted (only archived) and count against property quota → effectively irreversible | 🔴 | §5 tiers: writes 🟡/🔴 + `dry_run` |
| 3 | `ga4_universal_call` sends the user's **Bearer token to any `fullUrl`** (no host allow-list) and allows DELETE → token-exfiltration path via prompt injection | 🔴 | §5/§6: allow-list `*.googleapis.com`, GET-only or 🔴 confirm |
| 4 | No input validation; `propertyId` interpolated straight into URL paths | 🟡 | §5 validate every input (numeric IDs) |
| 5 | No tests, no CI | 🟠 | §3 pytest + mocked API |
| 6 | No `pageToken` support (0 tools), no retry/backoff on 429/5xx, no quota awareness | 🟡 | §5 pagination + `common/http.py` backoff |
| 7 | Returns raw API JSON (verbose; 1000-row default) — relies on 60k-char truncation | 🟡 | §5 compact structured output |
| 8 | Single-user, local, stdio-only; secrets in plaintext JSON; no audit log | 🟠 for us | §3 remote HTTP + per-user tokens + audit log |
| 9 | README claims refresh token "never expires" — but an External OAuth app in **Testing** status (as the README instructs) issues 7-day refresh tokens; must publish to Production to avoid weekly `invalid_grant` | 🟡 | document in our auth notes |
| 10 | Health check uses metric `conversions` (renamed `keyEvents` in Data API) — verify it still resolves | ℹ️ | verify at build time |
| 11 | `server.js` reports version 1.0.0 while package is 1.0.4; no `instructions`, no resources/prompts | ℹ️ | cosmetic |

## Decisions for our `ga4-mcp` (proposed)
- Port the **tool catalogue** (6 Data + 21 Admin + 4 helpers) as the target list; implement reads first (🟢), writes later (🟡/🔴 + `dry_run`).
- Reuse the ideas: endpoint routing by path, error shape, truncation, helper tools, env-override for multi-account.
- Replace: broad scopes → readonly default; `universal_call` → host-allow-listed, GET-only `ga4_raw_get` (🟢) and nothing else; add pagination, backoff, validation, tests, annotations.
- Auth for our remote deployment is different by design (server-held Google creds + per-user access tokens) — the desktop loopback flow is only for the one-time bootstrap script.
