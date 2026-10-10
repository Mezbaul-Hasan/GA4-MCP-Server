# GA4 MCP Server (marketing-mcp)

[![CI](https://github.com/Mezbaul-Hasan/GA4-MCP-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/Mezbaul-Hasan/GA4-MCP-Server/actions/workflows/ci.yml)

Custom [MCP](https://modelcontextprotocol.io) servers for marketing & advertising platforms, built for the owner's team.
First server: **Google Analytics 4** — 25 read-only tools, local stdio, Google OAuth browser sign-in.
Rules, scope and status live in **[PROJECT.md](PROJECT.md)** (source of truth).

| Folder | Content |
|---|---|
| `servers/ga4-mcp/` | Google Analytics 4 server — tools, CLI `ga4-mcp auth/status/logout/serve` → **[setup guide](servers/ga4-mcp/README.md)** |
| `common/` | shared Python package `marketing-mcp-common` (Google OAuth desktop flow, httpx client with retry, error mapping, redaction, output budget, audit log) |
| `docs/platform-notes/` | verified API facts per platform with sources |
| `.github/workflows/ci.yml` | tests on Ubuntu/Windows/macOS (Python 3.11 & 3.13) + `uvx` install check |

## Quick start (teammates)
1. Install [uv](https://docs.astral.sh/uv/) once.
2. Sign in with your own Google account (needs the team's OAuth client id/secret — ask the owner):
   `uvx --from "git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server#subdirectory=servers/ga4-mcp" ga4-mcp auth`
3. Add to Claude Desktop / Cursor:
   ```json
   { "mcpServers": { "ga4": { "command": "uvx",
     "args": ["--from", "git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server#subdirectory=servers/ga4-mcp", "ga4-mcp"] } } }
   ```
Full guide with the Google Cloud steps: [servers/ga4-mcp/README.md](servers/ga4-mcp/README.md).

## Development
```bash
pip install -e "common[dev]" -e "servers/ga4-mcp[dev]"
(cd common && pytest -q) && (cd servers/ga4-mcp && pytest -q)
```
License: MIT.
