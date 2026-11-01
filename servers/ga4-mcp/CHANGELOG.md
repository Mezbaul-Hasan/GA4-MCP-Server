# Changelog — ga4-mcp

## 0.1.0 — 2026-10-10
- First read-only release (Phase 1): 6 Data API tools, 15 Admin API tools (3 via v1alpha), 4 helpers — 25 tools, all `readOnlyHint=true`.
- Google OAuth desktop sign-in (`ga4-mcp auth`) with loopback redirect + PKCE, scope `analytics.readonly` only; `status` / `logout` / `serve` commands.
- Compact outputs (≤ 50 KB), filter/order-by shortcuts, pagination, retry with backoff, error → `ToolError` mapping, local audit log.
