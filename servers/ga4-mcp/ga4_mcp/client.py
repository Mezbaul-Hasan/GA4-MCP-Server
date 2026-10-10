"""Thin GA4 API client: routes to the Data API or Admin API and maps errors to ToolError."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from marketing_mcp_common.errors import AuthError, PlatformHTTPError, ToolError, google_error_to_tool_error
from marketing_mcp_common.http import HostNotAllowedError, HttpClient

from . import config

GA4_HINTS: dict[int, str] = {
    400: "check dimension/metric names with ga4_get_metadata or ga4_check_compatibility, and date/filter syntax",
    401: "the Google token was rejected — run `ga4-mcp auth` again",
    403: "this Google account lacks access to the property, or the Google Analytics Data/Admin API is not enabled in the OAuth client's Cloud project, or the scope is read-only for a write call",
    404: "property/account/stream id is wrong — use ga4_quick_overview or ga4_find_property to find the right id",
    429: "GA4 quota exhausted for this property/hour (tokens or concurrent requests) — wait a few minutes, request fewer rows/columns, or pass return_property_quota=true to inspect the quota",
    500: "temporary Google Analytics error — retry in a minute",
    503: "Google Analytics is temporarily unavailable — retry in a minute",
}


def _wrap(exc: Exception) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, AuthError):
        return ToolError(f"GA4 auth: {exc}")
    if isinstance(exc, HostNotAllowedError):
        return ToolError(f"GA4 raw request refused: {exc}")
    if isinstance(exc, PlatformHTTPError):
        return google_error_to_tool_error(exc, platform="GA4", hints=GA4_HINTS)
    return ToolError(f"GA4 request failed: {type(exc).__name__}: {exc}")


class GA4Client:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    # --------------------------------------------------------------- Data API
    async def data_post(self, prop_id: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``/v1beta/properties/{id}:{method}`` (runReport, runRealtimeReport, …)."""
        url = f"{config.DATA_API_BASE}/{config.DATA_API_VERSION}/properties/{prop_id}:{method}"
        try:
            return (await self.http.post_json(url, json=body)) or {}
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise _wrap(exc) from exc

    async def data_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{config.DATA_API_BASE}/{config.DATA_API_VERSION}/{path.lstrip('/')}"
        try:
            return (await self.http.get_json(url, params=params)) or {}
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

    # --------------------------------------------------------------- Admin API
    async def admin_get(self, path: str, params: dict[str, Any] | None = None, *, version: str = config.ADMIN_API_VERSION) -> dict[str, Any]:
        url = f"{config.ADMIN_API_BASE}/{version}/{path.lstrip('/')}"
        try:
            return (await self.http.get_json(url, params=params)) or {}
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

    async def admin_list(
        self,
        path: str,
        items_key: str,
        *,
        page_size: int,
        page_token: str | None,
        params: dict[str, Any] | None = None,
        version: str = config.ADMIN_API_VERSION,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"pageSize": page_size}
        if page_token:
            query["pageToken"] = page_token
        if params:
            query.update({k: v for k, v in params.items() if v is not None})
        data = await self.admin_get(path, query, version=version)
        items = data.get(items_key) or []
        return list(items), data.get("nextPageToken") or None

    async def admin_list_all(self, path: str, items_key: str, *, page_size: int = 200, params: dict[str, Any] | None = None, max_pages: int = 10, version: str = config.ADMIN_API_VERSION) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(max_pages):
            page, token = await self.admin_list(path, items_key, page_size=page_size, page_token=token, params=params, version=version)
            items.extend(page)
            if not token:
                break
        return items

    # --------------------------------------------------------------- raw GET
    async def raw_get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET any URL on an allow-listed Google Analytics host (never other hosts)."""
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in config.ALLOWED_HOSTS:
            raise ToolError(
                "ga4_raw_get only accepts https URLs on "
                + " or ".join(sorted(config.ALLOWED_HOSTS))
                + f" — got host {parts.hostname!r}"
            )
        try:
            return await self.http.get_json(url, params=params)
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
