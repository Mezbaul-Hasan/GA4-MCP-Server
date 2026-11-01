# GA4 MCP Server (marketing-mcp)

[![CI](https://github.com/Mezbaul-Hasan/GA4-MCP-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/Mezbaul-Hasan/GA4-MCP-Server/actions/workflows/ci.yml)

Custom [MCP](https://modelcontextprotocol.io) servers for marketing & advertising platforms, built for the owner's team.
This repository: **Google Analytics 4** (`ga4-mcp`, v0.1.0) — 25 read-only tools, local stdio, Google OAuth browser
sign-in — plus the shared library `marketing-mcp-common`. Every other server lives in its **own repository** under
[github.com/Mezbaul-Hasan](https://github.com/Mezbaul-Hasan) and installs `marketing-mcp-common` from a tag of this repo.

Rules, scope and status live in **[PROJECT.md](PROJECT.md)** (source of truth for this repo and the shared conventions).

| Folder | Content |
|---|---|
| `servers/ga4-mcp/` | Google Analytics 4 server — tools, CLI `ga4-mcp auth/status/logout/serve` → **[setup guide](servers/ga4-mcp/README.md)** |
| `common/` | shared Python package `marketing-mcp-common` (Google OAuth desktop flow, httpx client with retry + rate limiter, error mapping, redaction, output budget, audit log). Other server repos install it from a tag of this repo (`git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server@common-v0.1.1#subdirectory=common`) |
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
Releasing a new `common` version for the other server repos: bump `common/pyproject.toml`, push, tag `common-vX.Y.Z`,
then bump the tag in each server repo's `pyproject.toml`.

License: MIT.
