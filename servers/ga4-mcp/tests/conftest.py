"""Shared fixtures: in-process MCP client, fake token provider, respx-mocked Google APIs."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from mcp.client import Client

from ga4_mcp import config
from ga4_mcp.server import build_deps, create_server
from marketing_mcp_common.http import HttpClient

DATA = config.DATA_API_BASE
ADMIN = config.ADMIN_API_BASE
FAKE_TOKEN = "ya29.test-access-token-should-never-appear-in-logs"


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.force_refreshes = 0

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        self.calls += 1
        if force_refresh:
            self.force_refreshes += 1
        return FAKE_TOKEN


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def token_provider() -> FakeTokenProvider:
    return FakeTokenProvider()


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config.ENV_CONFIG_DIR, str(tmp_path))
    return tmp_path


@pytest.fixture
def server(token_provider: FakeTokenProvider, config_dir: Path):
    http = HttpClient(
        token_provider=token_provider,
        allowed_hosts=config.ALLOWED_HOSTS,
        backoff_base=0.0,
        backoff_cap=0.0,
        user_agent="ga4-mcp-tests",
    )
    deps = build_deps(token_provider=token_provider, config_dir=config_dir, audit_enabled=True, http=http)
    return create_server(deps)


@pytest.fixture
async def client(server) -> AsyncIterator[Client]:
    async with Client(server) as c:
        yield c


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


async def call(client: Client, name: str, args: dict[str, Any] | None = None) -> tuple[bool, Any]:
    """Call a tool; return (is_error, parsed JSON or error text)."""
    result = await client.call_tool(name, args or {})
    text = result.content[0].text if result.content else ""
    if result.is_error:
        return True, text
    try:
        return False, json.loads(text)
    except ValueError:
        return False, text


def audit_lines(config_dir: Path) -> list[dict[str, Any]]:
    path = config_dir / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ----------------------------------------------------------------------------- fixtures (API shapes)
def report_response(rows: list[tuple[list[str], list[str]]], *, dims: list[str], mets: list[str], row_count: int | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dimensionHeaders": [{"name": d} for d in dims],
        "metricHeaders": [{"name": m, "type": "TYPE_INTEGER"} for m in mets],
        "rows": [
            {"dimensionValues": [{"value": x} for x in dv], "metricValues": [{"value": x} for x in mv]}
            for dv, mv in rows
        ],
        "rowCount": row_count if row_count is not None else len(rows),
        "metadata": {"currencyCode": "BDT", "timeZone": "Asia/Dhaka"},
        "kind": "analyticsData#runReport",
    }
    body.update(extra)
    return body


ACCOUNT_SUMMARIES = {
    "accountSummaries": [
        {
            "name": "accountSummaries/111",
            "account": "accounts/111",
            "displayName": "Agency Account",
            "propertySummaries": [
                {"property": "properties/320448620", "displayName": "Shop BD — GA4", "propertyType": "PROPERTY_TYPE_ORDINARY", "parent": "accounts/111"},
                {"property": "properties/555", "displayName": "Blog", "propertyType": "PROPERTY_TYPE_ORDINARY", "parent": "accounts/111"},
            ],
        }
    ]
}

PROPERTY = {
    "name": "properties/320448620",
    "displayName": "Shop BD — GA4",
    "propertyType": "PROPERTY_TYPE_ORDINARY",
    "timeZone": "Asia/Dhaka",
    "currencyCode": "BDT",
    "serviceLevel": "GOOGLE_ANALYTICS_STANDARD",
    "parent": "accounts/111",
    "account": "accounts/111",
    "createTime": "2024-01-01T00:00:00Z",
}

STREAMS = {
    "dataStreams": [
        {
            "name": "properties/320448620/dataStreams/987",
            "type": "WEB_DATA_STREAM",
            "displayName": "Website",
            "webStreamData": {"measurementId": "G-ABC123", "defaultUri": "https://shop.example"},
            "createTime": "2024-01-01T00:00:00Z",
        }
    ]
}

KEY_EVENTS = {
    "keyEvents": [
        {"name": "properties/320448620/keyEvents/1", "eventName": "purchase", "countingMethod": "ONCE_PER_EVENT", "custom": False, "deletable": True},
    ]
}

RETENTION = {"name": "properties/320448620/dataRetentionSettings", "eventDataRetention": "TWO_MONTHS", "userDataRetention": "TWO_MONTHS", "resetUserDataOnNewActivity": True}
