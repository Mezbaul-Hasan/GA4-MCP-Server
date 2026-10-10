# marketing-mcp-common

Shared, platform-agnostic building blocks used by every server under `servers/`.
Nothing in here knows about GA4, GTM, Google Ads … specifically — servers add the
platform logic on top. **Never copy these modules into a server; import them.**

| Module | What it gives you |
|---|---|
| `google_auth.py` | Google OAuth 2.0 *desktop* flow (loopback redirect + PKCE + state), local credential store (`~/.<app>/config.json`, mode 0600, env-var override), access-token cache/refresh (sync + async) |
| `http.py` | `HttpClient` on top of `httpx`: bearer auth, 30 s timeout, retry with exponential backoff on 429/5xx honouring `Retry-After`, host allow-list, stderr logging without payloads |
| `errors.py` | `PlatformHTTPError` and `google_error_to_tool_error()` → `ToolError("<what happened> — <what to do next>")` |
| `redact.py` | strips Google/OAuth tokens and secrets from any string or mapping before it reaches a log or audit line |
| `logging.py` | `get_logger()` — stderr-only logger (stdio rule) with redaction applied to every record |
| `pagination.py` | `validate_page_size()`, `enforce_output_budget()` (keeps tool output ≤ 50 KB and marks truncation), number parsing |
| `audit.py` | `AuditLog` — one JSON line per tool call in `~/.<app>/audit.jsonl` (tool, redacted args, duration, status) |

Install for development from the repo root:

```bash
pip install -e common -e servers/ga4-mcp
```
