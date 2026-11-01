"""Build the ga4-mcp MCPServer: wires auth → http → client → tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.audit import AuditLog
from marketing_mcp_common.google_auth import GoogleCredentialStore, GoogleTokenProvider
from marketing_mcp_common.http import HttpClient, TokenProvider
from marketing_mcp_common.logging import get_logger

from . import config
from .client import GA4Client
from .tools import admin, helpers, reports
from .tools._shared import Deps

log = get_logger("ga4_mcp")

INSTRUCTIONS = """Google Analytics 4 (GA4) read-only server. Reports come from the Data API, configuration from the Admin API.
Workflow: (1) if no property_id is known call ga4_quick_overview or ga4_find_property; (2) run ga4_run_report
(default: last 7 days ending yesterday, reports are in the property's own time zone); (3) when a report fails with
INVALID_ARGUMENT use ga4_get_metadata / ga4_check_compatibility to fix dimension or metric names.
Important names: metric 'keyEvents' (not 'conversions'), 'sessionDefaultChannelGroup', 'sessionSource', 'pagePath',
'date' (YYYYMMDD in results). All tools are read-only; the person's own Google permissions decide what is visible.
If a tool answers 'not signed in', the user must run `ga4-mcp auth` in a terminal once."""


def build_deps(*, token_provider: TokenProvider, config_dir: Path | None = None, audit_enabled: bool | None = None, http: HttpClient | None = None) -> Deps:
    cfg_dir = config_dir or config.config_dir()
    http_client = http or HttpClient(
        token_provider=token_provider,
        allowed_hosts=config.ALLOWED_HOSTS,
        user_agent=f"{config.APP_NAME}/{config.VERSION}",
        logger_name="ga4_mcp.http",
    )
    audit = AuditLog(cfg_dir / "audit.jsonl", enabled=config.audit_enabled() if audit_enabled is None else audit_enabled)
    return Deps(client=GA4Client(http_client), audit=audit)


def create_server(deps: Deps | None = None) -> MCPServer:
    """Return a ready MCPServer. ``deps`` can be injected for tests."""
    if deps is None:
        store = GoogleCredentialStore(config.config_dir(), env_prefix="GOOGLE")
        provider = GoogleTokenProvider(store, app_name=config.APP_NAME)
        deps = build_deps(token_provider=provider)
    assert deps is not None

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[Deps]:
        try:
            yield deps
        finally:
            await deps.client.http.aclose()

    mcp = MCPServer(
        name=config.APP_NAME,
        title="Google Analytics 4",
        version=config.VERSION,
        instructions=INSTRUCTIONS,
        website_url="https://developers.google.com/analytics/devguides/reporting/data/v1",
        lifespan=lifespan,
    )
    reports.register(mcp, deps)
    admin.register(mcp, deps)
    helpers.register(mcp, deps)
    return mcp


def run_stdio() -> None:
    server = create_server()
    log.info("%s %s starting on stdio (config dir %s)", config.APP_NAME, config.VERSION, config.config_dir())
    server.run(transport="stdio")
