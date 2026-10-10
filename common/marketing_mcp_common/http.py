"""Async HTTP client shared by all servers.

Features (PROJECT.md §3/§5): bearer auth via a pluggable token provider, 30 s timeout,
retry with exponential backoff + jitter on 429/5xx and transport errors (honouring
``Retry-After``), one forced token refresh on 401, optional host allow-list, and
stderr logging of *method · host/path · status · duration* only — never payloads,
query strings or credentials.
"""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Iterable
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .errors import PlatformHTTPError
from .logging import get_logger

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_REQUEST_ID_HEADERS = ("x-request-id", "x-goog-request-id", "x-guploader-uploadid")


class TokenProvider(Protocol):
    async def get_access_token(self, *, force_refresh: bool = False) -> str: ...


class HostNotAllowedError(ValueError):
    """The URL host is not in the client's allow-list."""


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` may be seconds or an HTTP-date; return seconds (>= 0) or ``None``."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, dt.timestamp() - time.time())


class HttpClient:
    def __init__(
        self,
        *,
        token_provider: TokenProvider | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        allowed_hosts: Iterable[str] | None = None,
        user_agent: str = "marketing-mcp/0.1",
        logger_name: str = "marketing_mcp.http",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._allowed_hosts = frozenset(h.lower() for h in allowed_hosts) if allowed_hosts else None
        self._user_agent = user_agent
        self._log = get_logger(logger_name)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ lifecycle
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent, "Accept": "application/json"},
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ helpers
    def check_host(self, url: str) -> str:
        """Validate the URL scheme/host against the allow-list; return the host."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https":
            raise HostNotAllowedError("only https:// URLs are allowed")
        if self._allowed_hosts is not None and host not in self._allowed_hosts:
            raise HostNotAllowedError(f"host '{host}' is not allowed (allowed: {', '.join(sorted(self._allowed_hosts))})")
        return host

    @staticmethod
    def _safe_path(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.hostname}{parts.path}"

    @staticmethod
    def _parse_body(response: httpx.Response) -> Any:
        if not response.content:
            return None
        ctype = response.headers.get("content-type", "")
        if "json" in ctype:
            try:
                return response.json()
            except ValueError:
                return response.text
        return response.text

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self._backoff_cap)
        return min(self._backoff_base * (2**attempt) + random.uniform(0, 0.5), self._backoff_cap)

    # ------------------------------------------------------------------ requests
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> Any:
        """Send a request and return the parsed JSON body (``None`` for empty bodies).

        Raises :class:`PlatformHTTPError` for non-2xx responses after retries are exhausted.
        """
        self.check_host(url)
        client = self._get_client()
        safe = self._safe_path(url)
        attempt = 0
        force_refresh = False
        refreshed_once = False
        while True:
            req_headers = dict(headers or {})
            if auth and self._token_provider is not None:
                token = await self._token_provider.get_access_token(force_refresh=force_refresh)
                req_headers["Authorization"] = f"Bearer {token}"
                force_refresh = False
            started = time.monotonic()
            try:
                response = await client.request(method, url, params=params, json=json, headers=req_headers)
            except httpx.TransportError as exc:
                elapsed = (time.monotonic() - started) * 1000
                if attempt < self._max_retries:
                    delay = self._backoff(attempt, None)
                    self._log.warning("%s %s transport error %s after %.0f ms — retry %d in %.1fs", method, safe, type(exc).__name__, elapsed, attempt + 1, delay)
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise PlatformHTTPError(0, f"network error: {type(exc).__name__}", method=method, path=safe) from exc

            elapsed = (time.monotonic() - started) * 1000
            status = response.status_code
            request_id = next((response.headers[h] for h in _REQUEST_ID_HEADERS if h in response.headers), None)
            self._log.info("%s %s -> %s (%.0f ms)%s", method, safe, status, elapsed, f" rid={request_id}" if request_id else "")

            if 200 <= status < 300:
                return self._parse_body(response)

            if status == 401 and auth and self._token_provider is not None and not refreshed_once:
                refreshed_once = True
                force_refresh = True
                continue

            if status in RETRY_STATUSES and attempt < self._max_retries:
                delay = self._backoff(attempt, parse_retry_after(response.headers.get("retry-after")))
                self._log.warning("%s %s -> %s — retry %d in %.1fs", method, safe, status, attempt + 1, delay)
                attempt += 1
                await asyncio.sleep(delay)
                continue

            body = self._parse_body(response)
            message = response.reason_phrase or "request failed"
            code: str | None = None
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    message = str(err.get("message") or message)
                    code = err.get("status")
                elif isinstance(err, str):
                    message = str(body.get("error_description") or err)
                    code = err
            raise PlatformHTTPError(status, message, code=code, body=body, request_id=request_id, method=method, path=safe)

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        return await self.request_json("GET", url, params=params, **kw)

    async def post_json(self, url: str, *, json: Any = None, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        return await self.request_json("POST", url, json=json, params=params, **kw)
