# PROJECT.md — Marketing & Ads MCP Servers

> Repo: https://github.com/Mezbaul-Hasan/GA4-MCP-Server · CI: GitHub Actions (tests on Ubuntu/Windows/macOS + `uvx` install check)

> **🇧🇩 এই ফাইলটি কীভাবে ব্যবহার করবেন:** প্রতিটি নতুন কাজের শুরুতে Arena এজেন্টকে বলুন
> **"PROJECT.md পড়ে কাজ করো"**। এজেন্ট নিচের নিয়ম, কাঠামো ও status অনুযায়ী কাজ করবে।
> মালিকের তথ্য ও সিদ্ধান্ত §1, §11-এ লেখা আছে (২০২৬-১০-১০ তারিখে পূরণ করা)। সিদ্ধান্ত বদলালে এই ফাইল আপডেট করুন —
> এটাই প্রজেক্টের একমাত্র "source of truth"। কোনো কাজ শেষ হলে §2 (status) ও §10 (progress log) আপডেট হবে।

---

## 1. Mission
Build a family of production-quality **custom MCP servers** so that AI agents (Claude Desktop, Cursor, VS Code, Arena, or a custom agent) can **read data from** and — only with explicit confirmation — **act on** marketing & advertising platforms.

- **Owner:** Md. Mezbaul Hasan · **Timezone:** Asia/Dhaka · **Started:** 2026-10-10
- **Chat language:** Bengali (বাংলা). **Code, comments, docstrings, commit messages, READMEs:** English.
- **Primary users of the servers:** the owner's **team/agency** (several people) using the team's **own** platform accounts. No client accounts for now — if that changes, add per-client credentials, a separate deployment, and an ADR first.
- **Success looks like:** an agent can answer "how did campaign X perform last 7 days?" across platforms in one conversation, and can prepare (not silently execute) changes such as pausing a campaign or publishing a GTM version.

## 2. Scope — servers to build (priority order)
| # | Server folder | Platform API | Auth | Main use | Status |
|---|---|---|---|---|---|
| 1 | `ga4-mcp` | GA4 Data API v1beta + Admin API v1beta (+v1alpha reads) | Google OAuth2 (desktop browser flow, `analytics.readonly`) | reporting + config reads (25 🟢 tools) | 🟢 v0.1.0 — mocked tests green (60); **live check by owner pending** |
| 2 | `gtm-mcp` | Google Tag Manager API v2 | Google OAuth2 | audit tags/triggers/variables; publish versions (🔴) | ⬜ |
| 3 | `google-ads-mcp` | Google Ads API (GAQL) | OAuth2 + developer token (+ MCC login-customer-id) | reports; campaign/budget changes (🔴) | ⬜ |
| 4 | `meta-mcp` | Meta Marketing API (Graph API) | System-user token / OAuth | insights; campaign management (🔴) | ⬜ |
| 5 | `openai-mcp` | OpenAI API | API key | text/image/analysis as tools; **budget-capped** | ⬜ |
| 6 | `tiktok-mcp` | TikTok Marketing API | OAuth2 access token | reports; campaign management | ⬜ |
| 7 | `linkedin-mcp` | LinkedIn Marketing API | OAuth2 (partner/program access) | analytics; posts/campaigns | ⬜ |
| 8 | `pinterest-mcp` | Pinterest API v5 | OAuth2 | analytics; pins/boards/campaigns | ⬜ |
| 9 | `snapchat-mcp` | Snapchat Marketing API | OAuth2 | reports; campaign management | ⬜ |
| 10 | `x-mcp` | X Ads API / X API v2 | OAuth 2.0 (user context) / 1.0a | analytics; posting (paid tier) | ⬜ |
| … | *(future servers — add a new row here)* | | | | |

**Status legend:** ⬜ not started · 🟡 auth working · 🟢 read tools done · ✅ write tools + tests + docs complete

**Out of scope (for now):** building a dashboard/UI · billing or payment automation · scraping platforms without official API access · managing client credentials for third parties.

## 3. Tech decisions (do not change without updating this section + an ADR)
- **Language/SDK (owner decision 2026-10-10 — Python chosen over JavaScript):** Python 3.11+, MCP Python SDK **v2** (`mcp[cli]>=2,<3`) → `from mcp.server.mcpserver import MCPServer`; raise `ToolError` for anticipated failures. Reasons: most readable code for the owner, pandas for in-tool analysis, pytest with in-process `Client(server)`.
- **Layout:** one repo; one folder per server under `servers/`; shared code in `common/` (never copy-paste auth/http code between servers).
- **Transport:** **stdio, running on each user's own computer** (owner decision 2026-10-10, revised): Claude Desktop / Cursor / VS Code launches the server locally; credentials never leave that machine. No hosted server, no HTTP endpoint, no inbound ports. (Streamable HTTP = optional future work, out of scope now.)
- **Distribution:** teammates install **`uv` once** (one command on Windows/macOS), then one-line host config: `uvx --from git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server <server>` — same experience as the reference repo's `npx`. Every server is therefore a proper package with a `[project.scripts]` entry (`ga4-mcp = ga4_mcp.cli:main`) exposing `auth | status | logout | help | (run)`. Must be verified on Windows **and** macOS.
- **Build order per server:** build + test in Arena → install in the owner's Claude Desktop → verify with real data → share config snippet with the team.
- **HTTP client:** `httpx` with retry + exponential backoff; honour each platform's rate limits and `Retry-After`.
- **Config:** environment variables only; one `.env.example` per server; secrets never in code, logs, tests, or chat.
- **Tests:** `pytest` + in-process `Client(server)`; platform APIs **mocked** (e.g. `respx` or recorded JSON fixtures). No live API calls in tests.
- **Packaging:** `pyproject.toml` per server; runnable with `python server.py` and `mcp dev server.py`.
- **Versioning:** semantic versions per server; CHANGELOG entry for any tool signature change.

## 4. Repository layout
```
marketing-mcp/
├── PROJECT.md                      ← this file
├── common/                         # pip package `marketing-mcp-common` (import `marketing_mcp_common`)
│   ├── pyproject.toml
│   ├── marketing_mcp_common/
│   │   ├── google_auth.py          # Google OAuth desktop flow (loopback + PKCE), credential store, token provider — GA4, GTM, Google Ads
│   │   ├── oauth2.py               # (planned) generic authorization-code + refresh flow (Meta, TikTok, LinkedIn, Pinterest, Snap, X)
│   │   ├── http.py                 # httpx client: retry, backoff, Retry-After, host allow-list, timeouts
│   │   ├── errors.py               # PlatformHTTPError → ToolError mapping
│   │   ├── redact.py / logging.py  # strip tokens from logs; stderr-only logger
│   │   ├── pagination.py           # page_size / page_token helpers + 50 KB output budget
│   │   └── audit.py                # local audit.jsonl writer
│   └── tests/
├── servers/
│   ├── ga4-mcp/                    # pip package `ga4-mcp`, CLI `ga4-mcp` (auth|status|logout|serve|help)
│   │   ├── server.py               # runner for `python server.py` / `mcp dev server.py`
│   │   ├── ga4_mcp/                # config.py, validation.py, client.py, server.py (create_server), cli.py
│   │   │   └── tools/              # reports.py (Data API), admin.py (Admin API), helpers.py, _shared.py (audit decorator)
│   │   ├── tests/                  # in-process MCP client + respx-mocked Google APIs
│   │   ├── README.md               # setup (Cloud OAuth client, uv), tool table, config snippet, troubleshooting
│   │   ├── CHANGELOG.md · .env.example · pyproject.toml
│   ├── gtm-mcp/ … (same shape)
│   └── …
├── docs/
│   ├── platform-notes/<platform>.md   # endpoints used, scopes, quotas, quirks, gotchas
│   └── decisions/NNN-*.md             # ADRs
└── scripts/
    ├── <platform>_auth.py          # one-time auth bootstrap: prints refresh token to paste into .env
    └── smoke_<platform>.py         # manual live check (never run in CI)
```

## 5. Tool design conventions
- **Naming:** `<verb>_<resource>` — reads: `list_`, `get_`, `search_`, `run_report`, `query_`; writes: `create_`, `update_`, `pause_`, `resume_`, `publish_`, `delete_`.
- **Every tool has:** typed signature · docstring written *for the model* (purpose, each arg with an example value, what it returns, when NOT to use it) · annotations · input validation at the top.
- **Risk tiers** (set via `ToolAnnotations`, enforced by the host/agent policy):
  - 🟢 **READ** — `read_only_hint=True`: reports, lists, gets, metadata → may run automatically
  - 🟡 **WRITE** — `destructive_hint=False`: create draft objects, rename, add labels → **confirm**
  - 🔴 **SPEND / PUBLISH / DELETE** — `destructive_hint=True`: publish GTM version, change budgets/bids, enable or delete campaigns, post publicly, OpenAI calls above budget → **confirm every time**
- **`dry_run: bool = True`** on every write tool where the platform offers validation/preview; the tool returns a diff/preview and only mutates when `dry_run=False`.
- **Output:** compact structured dicts/lists — never raw API payloads > 50 KB; always paginate (`page_size`, `page_token`) and say when results are truncated.
- **Dates:** ISO 8601. Default range = last 7 days. Report timezone = the property/ad-account timezone (state it in the output); user's local timezone = Asia/Dhaka.
- **Money:** return platform micros/cents **and** a human value, always with currency code.
- **IDs:** accept both numeric IDs and resource names where platforms use both (Google Ads `customers/123/campaigns/456`).
- **Errors:** map to `ToolError("<what happened> — <what the model/user should do next>")`; include platform request-id when available; never include tokens or full URLs with secrets.
- **Logging:** stderr only (stdio rule) · log tool name, duration, status code, request-id — never payloads or credentials.

## 6. Auth & secrets conventions
- **Auth model = Google OAuth on the user's own computer** (owner decision 2026-10-10, like the reference repo): a one-time `auth` command opens the browser, the person signs in with **their own Google account**, and the refresh token is stored locally (`~/.<server>/config.json`, 0600; env-var override for multi-account). Consequence: GA4/GTM/Ads permissions are **the person's own Google permissions** — Google decides who sees which property/account. Note: an OAuth app left in *Testing* status issues 7-day refresh tokens — publish the consent screen to *Production* (or use Internal type on Workspace) to avoid weekly re-auth.
- **Google (GA4, GTM, Google Ads):** one shared OAuth helper in `common/`; **minimal scopes by default** — `analytics.readonly`; `tagmanager.readonly`; `adwords` (Google Ads has one scope). Edit scopes (`analytics.edit`, `tagmanager.edit.containers`, `tagmanager.publish`) only via an explicit **`auth --with-writes`** profile when write tools are actually needed. Service account supported for GA4 (add it to the property).
- **Non-Google platforms (Meta, TikTok, LinkedIn, Pinterest, Snapchat, X):** same local pattern — browser OAuth with that platform, token stored locally — using each platform's own OAuth; no Google involvement.
- **Google Ads extras:** `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC), per-call `customer_id`. Developer-token access level (test → basic → standard) limits what works — document which level is needed per tool.
- **Meta:** long-lived **system-user** token from Business Manager; `ads_read` for read servers, `ads_management` only for write tools; note App Review requirements for Live apps.
- **Others (TikTok, LinkedIn, Pinterest, Snapchat, X):** OAuth2 authorization-code via `common/oauth2.py`; `scripts/<platform>_auth.py` prints the refresh token once; automatic refresh at runtime.
- **OpenAI:** `OPENAI_API_KEY` + `OPENAI_DAILY_BUDGET_USD`; every tool estimates cost first and refuses above budget.
- **Server access:** not applicable — there is no hosted server. Access control = (1) each person's own Google/platform account permissions, (2) the scope profile chosen at `auth` (read-only default), (3) tool annotations + the host's confirmation for 🟡/🔴 tools, (4) a local audit log per machine (`~/.<server>/audit.jsonl`).
- **Rules:** never paste tokens into chat · read-only credentials first, write permissions only when write tools are built · one `.env.example` per server listing every variable with a comment · rotate any secret that was ever exposed.

### 6.1 Access roles — who may use which server (status: **Proposed**, 2026-10-10)
> Principle: *whoever needs it, by name, and only as much as needed.* A tool is a capability; capabilities are granted to **named people**, never to "the team" or to an AI. An AI agent always acts with one person's token and can never do more than that person may; 🔴 actions always need that person's confirmation.

| Role | Servers | Allowed tiers | Why |
|---|---|---|---|
| **Owner / Admin** (Md. Mezbaul Hasan) | all | 🟢 🟡 🔴 | owns tokens, secrets, deployments; confirms 🔴 actions |
| **Media buyer / Ads manager** | google-ads, meta, tiktok, snapchat, linkedin (ads), pinterest (ads), x (ads) | 🟢 🟡; 🔴 only 1–2 named seniors | budgets, bids, enabling campaigns = real money |
| **Analyst / Reporting** | ga4 + insights/report tools of every platform | 🟢 only | reporting never needs write access |
| **Web / Tracking specialist** | gtm, ga4 (admin) | 🟢 🟡; `publish_*` 🔴 only the lead | a bad tag publish breaks site tracking/performance |
| **Content / Social** | linkedin, x, pinterest, tiktok (organic) | 🟢; public posting 🔴 only approved people | public mistakes cannot be undone |
| **AI-assisted content/analysis users** | openai-mcp | per-user daily budget cap | every call costs money |
| **Developer** (building the servers) | staging + test accounts only | everything, in dev only | production tokens never on developer machines |

**Not users (for now):** clients (§11) · shared/common tokens (never) · interns/freelancers without a concrete need (if needed: time-boxed 🟢 token, e.g. 30 days) · AI agents as principals (no token of their own).

**Enforcement rules**
1. One named token per person; audit log records who called which tool, when, with what result.
2. Role → capability is enforced by **what each person installs and authorizes**: read-only scope profile by default; write tools only appear when the server is started with `--enable-writes` *and* the person authorized the edit scopes. Prompts alone never gate anything.
3. Everyone starts at 🟢; upgrade to 🟡/🔴 only on demonstrated need — never the reverse.
4. 🔴 = always confirm + `dry_run` first + log. No exceptions, including the owner.
5. Offboarding: revoke the token the same day. Quarterly review of who has what.
6. First 3 months: owner + 1–2 analysts (🟢) only, while servers mature; write tiers open afterwards.

## 7. Per-server Definition of Done
- [ ] `docs/platform-notes/<platform>.md` written **before coding**: endpoints, scopes, quotas, versioning policy, sandbox availability — with links to official docs
- [ ] Auth bootstrap script + README setup verified against a real account (by owner)
- [ ] ≥ 5 🟢 read tools covering core objects (accounts → campaigns/containers → details) + one reporting tool
- [ ] Write tools only where there is a concrete use case; all 🟡/🔴 with correct annotations + `dry_run`
- [ ] Input validation, pagination, rate-limit backoff, error mapping implemented via `common/`
- [x] Tests: mocked unit test per tool + one in-process smoke test; `pytest -q` green (ga4-mcp: 60 tests, CI on 3 OSes)
- [ ] `README.md`: tool table (name · tier · description), env vars, scopes, Claude Desktop/Cursor config snippet (stdio **and** remote URL)
- [x] Installable: one-line host config (`uvx --from git+https://… <server>`) verified in CI on Windows, macOS and Ubuntu (ga4-mcp); `auth`, `status`, `logout` commands exist — live `auth` check by owner pending
- [ ] Status updated in §2; entry added to §10

## 8. How the Arena agent must work on this project
1. **Start every task** by reading this file; state which server/tools the task touches and the current status from §2.
2. **Research before coding:** look up the *current* official API docs (versions and quotas change), write/refresh `docs/platform-notes/<platform>.md`, cite sources.
3. **Plan → list files to create/change → build in small steps → run tests after each step.** Show the plan first for anything touching more than one server or `common/`.
4. **Ask before:** adding dependencies · any live API call that creates/changes/deletes data or spends money · deleting files · changing §3 decisions · exposing a server over HTTP.
5. **Never:** hard-code or log secrets · ask the owner to paste credentials into chat · call write endpoints without a `dry_run` path · mark work done without running tests · copy auth/http code between servers instead of using `common/`.
6. **Report format (in Bengali, code in English):** সারাংশ → পরিবর্তিত ফাইল → কীভাবে যাচাই করা হলো (test output) → খোলা প্রশ্ন/ঝুঁকি.
7. **End of task:** update §2 status and append to §10 progress log; download-worthy deliverables named clearly.
8. Arena notes: files persist in this conversation, installed packages may not — keep each server's `pyproject.toml` complete so dependencies can be reinstalled; keep heavy data out of the workspace.

## 9. Platform gotchas (verify at build time, extend as you learn)
- **GA4:** Data API quota tokens per property (hourly/daily); core vs realtime reports; dimension/metric compatibility must be checked; thresholding can hide rows; `propertyId` numeric.
- **GTM:** hierarchy account → container → **workspace** → version; edits live in a workspace until a version is created and **published** (🔴); rollback = republish an older version; API quotas are low — batch reads carefully.
- **Google Ads:** GAQL for all reads; mutations batched with partial-failure mode; `login-customer-id` for MCC; developer-token level gates production accounts; API versions sunset roughly yearly.
- **Meta:** Graph API versions deprecate on a schedule — pin version in env; ad-account-level rate limits (BUC); large insights ranges need async report jobs; attribution windows affect numbers.
- **OpenAI:** every call costs money — budget cap, token limits, and log usage; never send client PII without owner approval.
- **TikTok / Snapchat / Pinterest / LinkedIn / X:** app approval or partner-program access required; sandbox ≠ production; posting/publishing endpoints often need extra review or a paid tier (X); LinkedIn uses month-versioned API headers.

## 10. Progress log
| Date | Server | What happened | Next step |
|---|---|---|---|
| 2026-10-10 | — | PROJECT.md created; scope & conventions agreed | Start `ga4-mcp` (read-only): platform notes → auth → 5 read tools |
| 2026-10-10 | — | Owner decisions recorded: owner name, team use, own test accounts, remote hosting from day one (§3, §6, §11 updated) | Begin `ga4-mcp` locally (stdio), then deploy over HTTP |
| 2026-10-10 | — | §6.1 Access roles added (Proposed): role → servers → tiers, enforcement rules, first-3-months read-only policy | Owner to confirm roles/names; then start `ga4-mcp` |
| 2026-10-10 | — | Owner revised decisions: **local stdio on each person's computer, Google OAuth browser flow** (no hosted server) → §3, §6, §6.1, §7, §11 updated | Choose implementation language (JS vs Python) → start `ga4-mcp` |
| 2026-10-10 | — | **Language decided: Python** (MCP Python SDK v2); distribution via `uvx` + `[project.scripts]` CLI (`auth/status/logout`) | Start `ga4-mcp`: platform notes → `common/` (google_auth, http) → read tools |
| 2026-10-10 | ga4-mcp | Reference review of `mnsmasum62786/was-ga4-mcp` (31-tool Node GA4 MCP) → `docs/platform-notes/ga4-reference-was-ga4-mcp.md`: reuse tool catalogue + helpers; fix scopes, annotations, universal_call, pagination, tests | Write GA4 platform notes → start `ga4-mcp` read tools |
| 2026-10-10 | ga4-mcp | `docs/platform-notes/ga4.md` written (API versions, v1beta vs v1alpha resources, OAuth desktop flow + 7-day Testing-status warning, quotas, `keyEvents`, error mapping, tool list) | Build `common/` + `ga4-mcp` |
| 2026-10-10 | ga4-mcp | **v0.1.0 built (Phase 1, read-only):** `common/marketing_mcp_common` (google_auth, http, errors, redact, logging, pagination, audit) + `servers/ga4-mcp` with 25 🟢 tools (6 Data, 15 Admin, 4 helpers), CLI `ga4-mcp auth/status/logout/serve`, README, CHANGELOG, `.env.example`. Tests: 16 (common, incl. simulated loopback OAuth) + 44 (ga4-mcp) = 60 green, no network. Not yet done: live run against the owner's property, `uvx` install check on Windows/macOS | Owner: create Desktop OAuth client → `ga4-mcp auth` → `ga4_quick_overview` in Claude Desktop → report issues; then decide Phase 2 (write tools) or start `gtm-mcp` |
| 2026-10-10 | ga4-mcp | **Pushed to GitHub: https://github.com/Mezbaul-Hasan/GA4-MCP-Server (public, MIT, CI added).** CI run green on all 9 jobs: pytest on Ubuntu/Windows/macOS × Python 3.11/3.13 and `uvx --from git+…#subdirectory=servers/ga4-mcp ga4-mcp help` on all three OSes (install path verified). Fixed on the way: loopback server skipped `getfqdn()` (macOS delay). Placeholders replaced with the real repo URL in READMEs/CLI | Owner: live test with own Google account (README §1); then Phase 2 or `gtm-mcp` |

## 11. Owner decisions (answered 2026-10-10)
- **Test accounts:** the owner's **own** GA4 property, Google Ads account and Meta ad account. IDs go into each server's `.env` — never into this file or chat. Start with read-only scopes.
- **Hosting (revised 2026-10-10):** **no hosted server.** Each person runs the servers on their own computer over stdio, authenticates with their own Google account (browser flow). Teammates install via a one-line host config.
- **Clients' accounts:** not in scope now. If added later: separate credentials per client, separate deployment, ADR first.
- **Build order:** as listed in §2 (GA4 → GTM → Google Ads → Meta → OpenAI → others). The owner may reorder by editing §2.

### To fill in later (only when we reach that step — ask the owner, store in `.env`)
- GA4 property ID · Google Ads customer ID / MCC ID · Meta ad account ID
- Google Cloud project for the OAuth Desktop client: one shared client for the team (recommended; owner-managed, consent screen in Production) or each person's own — owner decides at `ga4-mcp` setup
- GitHub org/repo name used in the install command (`uvx --from git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server <server>`)
