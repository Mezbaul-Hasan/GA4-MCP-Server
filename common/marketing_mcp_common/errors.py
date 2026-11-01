"""Platform error types and the mapping to MCP ``ToolError``.

Rule (PROJECT.md §5): every anticipated failure becomes
``ToolError("<what happened> — <what the model/user should do next>")`` and never leaks
tokens or full URLs that could carry secrets.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

__all__ = ["AuthError", "PlatformHTTPError", "ToolError", "google_error_to_tool_error"]


class AuthError(Exception):
    """Raised when no usable credentials exist or a refresh token was rejected."""


class PlatformHTTPError(Exception):
    """A non-2xx response (or transport failure) from a platform API."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        body: Any = None,
        request_id: str | None = None,
        method: str = "",
        path: str = "",
    ) -> None:
        super().__init__(f"HTTP {status_code} {code or ''} {message}".strip())
        self.status_code = status_code
        self.message = message
        self.code = code  # e.g. Google "status": PERMISSION_DENIED
        self.body = body
        self.request_id = request_id
        self.method = method
        self.path = path  # path only, never the query string


def parse_google_error(body: Any) -> tuple[str, str | None, list[Any]]:
    """Return ``(message, status, details)`` from a Google API error envelope.

    Handles both ``{"error": {"code", "message", "status", "details"}}`` (API calls) and
    ``{"error": "invalid_grant", "error_description": "..."}`` (OAuth token endpoint).
    """
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return (
                str(err.get("message") or "unknown error"),
                err.get("status"),
                list(err.get("details") or []),
            )
        if isinstance(err, str):
            return (str(body.get("error_description") or err), err, [])
    if isinstance(body, str) and body.strip():
        return (body.strip()[:300], None, [])
    return ("unknown error", None, [])


# Default, platform-neutral hints by HTTP status.
_DEFAULT_HINTS: dict[int, str] = {
    400: "check the request arguments (names, filters, dates)",
    401: "the access token was rejected — re-run the server's `auth` command",
    403: "you lack permission for this resource, the API is not enabled in the Cloud project, or the OAuth scope is insufficient",
    404: "the resource does not exist or the ID is wrong — list the parent resource first",
    409: "conflicts with an existing resource",
    429: "quota exhausted — wait and retry with a smaller request",
    500: "temporary platform error — retry later",
    502: "temporary platform error — retry later",
    503: "temporary platform error — retry later",
    504: "the platform timed out — reduce the request size and retry",
}


def google_error_to_tool_error(
    err: PlatformHTTPError,
    *,
    platform: str,
    hints: dict[int, str] | None = None,
) -> ToolError:
    """Convert a :class:`PlatformHTTPError` from a Google API into a ``ToolError``."""
    message, status, details = parse_google_error(err.body)
    status = status or err.code
    hint = (hints or {}).get(err.status_code) or _DEFAULT_HINTS.get(err.status_code, "retry or check the arguments")

    # Pull helpful field names out of ErrorInfo/BadRequest details when present.
    extra: list[str] = []
    for d in details:
        if not isinstance(d, dict):
            continue
        if d.get("reason"):
            extra.append(str(d["reason"]))
        for fv in d.get("fieldViolations") or []:
            if isinstance(fv, dict) and fv.get("field"):
                extra.append(f"{fv['field']}: {fv.get('description', '')}".strip())
    extra_txt = f" [{'; '.join(extra[:5])}]" if extra else ""
    rid = f" (request-id {err.request_id})" if err.request_id else ""
    return ToolError(f"{platform} API {err.status_code} ({status or 'ERROR'}): {message}{extra_txt}{rid} — {hint}")
