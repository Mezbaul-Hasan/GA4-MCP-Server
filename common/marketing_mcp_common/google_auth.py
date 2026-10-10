"""Google OAuth 2.0 for *locally running* MCP servers (PROJECT.md §6).

Flow (identical for GA4, GTM, Google Ads — only the scopes differ):

1. ``<server> auth`` (CLI, sync): :class:`GoogleOAuthDesktopFlow` opens the browser with a
   Desktop-app OAuth client, receives the code on a loopback redirect
   (``http://127.0.0.1:<random port>/callback``), verifies ``state``, exchanges the code with
   **PKCE (S256)** and stores ``client_id``/``client_secret``/``refresh_token`` in
   ``~/.<server>/config.json`` (dir 0700, file 0600) via :class:`GoogleCredentialStore`.
2. At runtime (async tools): :class:`GoogleTokenProvider` loads the credentials lazily,
   refreshes the access token (cached with a 5-minute margin) and raises
   :class:`~marketing_mcp_common.errors.AuthError` with a clear next step when the refresh
   token is missing/expired.

Environment override (multi-account / CI): ``GOOGLE_CLIENT_ID``, ``GOOGLE_CLIENT_SECRET``,
``GOOGLE_REFRESH_TOKEN`` (all three) win over the file. The config directory itself can be
moved with ``<APP>_CONFIG_DIR`` (handled by the server's config module).

⚠️ If the Cloud project's OAuth consent screen is *External* + **Testing**, Google issues
refresh tokens that expire after 7 days (the token response then contains
``refresh_token_expires_in``). We store that date, warn in ``auth``/``status`` and explain it
in the ``invalid_grant`` error. Fix: publish the consent screen to *In production*.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .errors import AuthError, parse_google_error
from .logging import get_logger

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

ACCESS_TOKEN_MARGIN_SECONDS = 300  # refresh 5 minutes before expiry
_LOOPBACK_HOST = "127.0.0.1"

_SUCCESS_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>{app} — connected</title>
<style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 20px;color:#1a1a1a}}
h1{{font-size:1.4rem}}code{{background:#f2f2f2;padding:2px 6px;border-radius:4px}}</style></head>
<body><h1>✅ {app} is connected to your Google account</h1>
<p>You can close this tab and go back to your terminal / AI client.</p></body></html>"""

_FAILURE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>{app} — failed</title></head>
<body style="font-family:system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 20px">
<h1>❌ Sign-in failed</h1><p>{reason}</p><p>Close this tab and run <code>{app} auth</code> again.</p></body></html>"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def mask(value: str | None, keep: int = 10) -> str:
    if not value:
        return ""
    return value[:keep] + "…" if len(value) > keep else value


# ----------------------------------------------------------------------------- credentials
@dataclass
class GoogleCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    scopes: list[str] = field(default_factory=list)
    created_at: str | None = None
    refresh_token_expires_at: str | None = None
    source: str = "file"  # "file" | "env"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("source", None)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any], *, source: str = "file") -> "GoogleCredentials":
        missing = [k for k in ("client_id", "client_secret", "refresh_token") if not data.get(k)]
        if missing:
            raise AuthError(f"credentials are incomplete (missing {', '.join(missing)})")
        scopes = data.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        return cls(
            client_id=str(data["client_id"]),
            client_secret=str(data["client_secret"]),
            refresh_token=str(data["refresh_token"]),
            scopes=list(scopes),
            created_at=data.get("created_at"),
            refresh_token_expires_at=data.get("refresh_token_expires_at"),
            source=source,
        )

    def refresh_token_days_left(self) -> int | None:
        if not self.refresh_token_expires_at:
            return None
        try:
            exp = datetime.fromisoformat(self.refresh_token_expires_at)
        except ValueError:
            return None
        return int((exp - _now()).total_seconds() // 86400)


class GoogleCredentialStore:
    """Reads/writes ``<config_dir>/config.json``; env vars override the file."""

    def __init__(self, config_dir: Path, *, env_prefix: str = "GOOGLE", filename: str = "config.json") -> None:
        self.config_dir = Path(config_dir)
        self.path = self.config_dir / filename
        self.env_prefix = env_prefix
        self._log = get_logger("marketing_mcp.auth")

    # env override -------------------------------------------------------------
    def _env(self, name: str) -> str | None:
        value = os.environ.get(f"{self.env_prefix}_{name}")
        return value.strip() if value else None

    def env_override_active(self) -> bool:
        return all(self._env(n) for n in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"))

    def load(self) -> GoogleCredentials | None:
        if self.env_override_active():
            return GoogleCredentials(
                client_id=self._env("CLIENT_ID") or "",
                client_secret=self._env("CLIENT_SECRET") or "",
                refresh_token=self._env("REFRESH_TOKEN") or "",
                scopes=(self._env("SCOPES") or "").split(),
                source="env",
            )
        if not self.path.exists():
            return None
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise AuthError(f"could not read {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise AuthError(f"{self.path} is not a JSON object")
        return GoogleCredentials.from_json(data, source="file")

    def save(self, creds: GoogleCredentials) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
        except OSError:
            pass
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(creds.to_json(), fh, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)
        return self.path

    def clear(self) -> bool:
        if self.path.exists():
            self.path.unlink()
            return True
        return False


# ----------------------------------------------------------------------------- PKCE helpers
def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
    login_hint: str | None = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def credentials_from_token_response(
    token: dict[str, Any], *, client_id: str, client_secret: str, requested_scopes: list[str]
) -> GoogleCredentials:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise AuthError(
            "Google did not return a refresh token — remove the app's access at "
            "https://myaccount.google.com/permissions and run auth again"
        )
    granted = str(token.get("scope") or " ".join(requested_scopes)).split()
    expires_at: str | None = None
    if token.get("refresh_token_expires_in"):
        try:
            expires_at = _iso(_now() + timedelta(seconds=int(token["refresh_token_expires_in"])))
        except (TypeError, ValueError):
            expires_at = None
    return GoogleCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=str(refresh_token),
        scopes=granted,
        created_at=_iso(_now()),
        refresh_token_expires_at=expires_at,
    )


# ----------------------------------------------------------------------------- desktop flow
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parts = urlsplit(self.path)
        if parts.path != self.server.callback_path:
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.server.result = query
        if query.get("error") or not query.get("code"):
            body = _FAILURE_HTML.format(app=self.server.app_name, reason=query.get("error", "no code received"))
            status = 400
        else:
            body = _SUCCESS_HTML.format(app=self.server.app_name)
            status = 200
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # silence stdout logging
        return


class _CallbackServer(http.server.HTTPServer):
    allow_reuse_address = True

    def __init__(self, app_name: str, callback_path: str) -> None:
        super().__init__((_LOOPBACK_HOST, 0), _CallbackHandler)
        self.app_name = app_name
        self.callback_path = callback_path
        self.result: dict[str, str] | None = None


class GoogleOAuthDesktopFlow:
    """Synchronous browser sign-in used only by the CLI ``auth`` command."""

    def __init__(
        self,
        *,
        app_name: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        timeout: float = 300.0,
        open_browser: bool = True,
        login_hint: str | None = None,
        callback_path: str = "/callback",
    ) -> None:
        self.app_name = app_name
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.scopes = scopes
        self.timeout = timeout
        self.open_browser = open_browser
        self.login_hint = login_hint
        self.callback_path = callback_path
        self._log = get_logger("marketing_mcp.auth")

    def run(self, *, echo: Any = print) -> GoogleCredentials:
        if not self.client_id or not self.client_secret:
            raise AuthError("client_id and client_secret are required")
        server = _CallbackServer(self.app_name, self.callback_path)
        port = server.server_address[1]
        redirect_uri = f"http://{_LOOPBACK_HOST}:{port}{self.callback_path}"
        state = secrets.token_urlsafe(24)
        verifier, challenge = make_pkce_pair()
        url = build_auth_url(
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            scopes=self.scopes,
            state=state,
            code_challenge=challenge,
            login_hint=self.login_hint,
        )
        echo("Opening your browser for Google sign-in. If nothing opens, copy this URL:\n\n" + url + "\n")
        opened = False
        if self.open_browser:
            try:
                opened = webbrowser.open(url, new=1, autoraise=True)
            except Exception:  # pragma: no cover - platform specific
                opened = False
        if not opened:
            echo("(Could not open a browser automatically — paste the URL above into any browser on this computer.)")
        echo(f"Waiting up to {int(self.timeout)} s for the sign-in to complete…")

        server.timeout = 1.0
        deadline = time.monotonic() + self.timeout
        try:
            while server.result is None and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if server.result is None:
            raise AuthError("timed out waiting for the browser sign-in — run auth again")
        result = server.result
        if result.get("error"):
            raise AuthError(f"Google returned '{result['error']}' — sign in again and click Allow")
        if result.get("state") != state:
            raise AuthError("state mismatch in the OAuth callback (possible CSRF) — run auth again")
        code = result.get("code")
        if not code:
            raise AuthError("no authorization code in the callback — run auth again")

        token = exchange_code(
            code=code,
            code_verifier=verifier,
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        return credentials_from_token_response(
            token, client_id=self.client_id, client_secret=self.client_secret, requested_scopes=self.scopes
        )


def exchange_code(*, code: str, code_verifier: str, client_id: str, client_secret: str, redirect_uri: str, timeout: float = 30.0) -> dict[str, Any]:
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    try:
        response = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=timeout)
    except httpx.TransportError as exc:
        raise AuthError(f"could not reach Google's token endpoint ({type(exc).__name__}) — check your internet connection") from exc
    if response.status_code != 200:
        message, status, _ = parse_google_error(_safe_json(response))
        raise AuthError(f"token exchange failed ({status or response.status_code}): {message}")
    return response.json()


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _refresh_error(status_code: int, body: Any, app_name: str, creds: GoogleCredentials) -> AuthError:
    message, status, _ = parse_google_error(body)
    if status == "invalid_grant":
        extra = ""
        if creds.refresh_token_expires_at:
            extra = " This OAuth client is in *Testing* status (7-day tokens) — publish the consent screen to production to stop weekly re-logins."
        return AuthError(f"Google rejected the refresh token (invalid_grant: {message}) — run `{app_name} auth` again.{extra}")
    if status == "invalid_client":
        return AuthError(f"OAuth client id/secret rejected (invalid_client) — run `{app_name} auth` with the correct Desktop-app client")
    return AuthError(f"token refresh failed ({status or status_code}): {message} — run `{app_name} auth` again")


# ----------------------------------------------------------------------------- runtime provider
class GoogleTokenProvider:
    """Caches/refreshes the access token; usable from async tools and the sync CLI."""

    def __init__(self, store: GoogleCredentialStore, *, app_name: str, http_timeout: float = 20.0) -> None:
        self.store = store
        self.app_name = app_name
        self._timeout = http_timeout
        self._creds: GoogleCredentials | None = None
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock | None = None
        self._tlock = threading.Lock()
        self._log = get_logger("marketing_mcp.auth")

    # credentials ---------------------------------------------------------------
    def credentials(self, *, reload: bool = False) -> GoogleCredentials:
        if self._creds is None or reload:
            creds = self.store.load()
            if creds is None:
                raise AuthError(
                    f"not signed in — run `{self.app_name} auth` once in a terminal (opens Google sign-in), "
                    f"or set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN"
                )
            self._creds = creds
        return self._creds

    def has_credentials(self) -> bool:
        try:
            return self.store.load() is not None
        except AuthError:
            return False

    def _token_valid(self) -> bool:
        return bool(self._access_token) and time.time() < self._expires_at - ACCESS_TOKEN_MARGIN_SECONDS

    def _refresh_payload(self) -> dict[str, str]:
        creds = self.credentials()
        return {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
            "grant_type": "refresh_token",
        }

    def _store_token(self, token: dict[str, Any]) -> str:
        access_token = token.get("access_token")
        if not access_token:
            raise AuthError(f"token refresh returned no access_token — run `{self.app_name} auth` again")
        self._access_token = str(access_token)
        self._expires_at = time.time() + float(token.get("expires_in") or 3600)
        self._log.debug("access token refreshed (expires in %ss)", token.get("expires_in"))
        return self._access_token

    # async (tools) ----------------------------------------------------------------
    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if not force_refresh and self._token_valid():
                return self._access_token  # type: ignore[return-value]
            payload = self._refresh_payload()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(GOOGLE_TOKEN_URL, data=payload)
            except httpx.TransportError as exc:
                raise AuthError(f"could not reach Google's token endpoint ({type(exc).__name__}) — check the internet connection") from exc
            if response.status_code != 200:
                raise _refresh_error(response.status_code, _safe_json(response), self.app_name, self.credentials())
            return self._store_token(response.json())

    # sync (CLI) -------------------------------------------------------------------
    def get_access_token_sync(self, *, force_refresh: bool = False) -> str:
        with self._tlock:
            if not force_refresh and self._token_valid():
                return self._access_token  # type: ignore[return-value]
            payload = self._refresh_payload()
            try:
                response = httpx.post(GOOGLE_TOKEN_URL, data=payload, timeout=self._timeout)
            except httpx.TransportError as exc:
                raise AuthError(f"could not reach Google's token endpoint ({type(exc).__name__}) — check the internet connection") from exc
            if response.status_code != 200:
                raise _refresh_error(response.status_code, _safe_json(response), self.app_name, self.credentials())
            return self._store_token(response.json())

    # status / logout --------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "app": self.app_name,
            "config_file": str(self.store.path),
            "env_override": self.store.env_override_active(),
            "signed_in": False,
        }
        try:
            creds = self.store.load()
        except AuthError as exc:
            info["error"] = str(exc)
            return info
        if creds is None:
            info["next_step"] = f"run `{self.app_name} auth`"
            return info
        info.update(
            {
                "signed_in": True,
                "source": creds.source,
                "client_id": mask(creds.client_id, 14),
                "scopes": creds.scopes,
                "created_at": creds.created_at,
                "refresh_token_expires_at": creds.refresh_token_expires_at,
            }
        )
        days = creds.refresh_token_days_left()
        if days is not None:
            info["refresh_token_days_left"] = days
            info["warning"] = (
                "OAuth consent screen is in Testing status → refresh token expires in 7 days; "
                "publish it to production (Google Cloud Console → OAuth consent screen) to avoid weekly re-login"
            )
        return info

    def logout(self, *, revoke: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {"revoked": False, "deleted_file": False}
        try:
            creds = self.store.load()
        except AuthError:
            creds = None
        if creds and revoke and creds.source == "file":
            try:
                response = httpx.post(GOOGLE_REVOKE_URL, data={"token": creds.refresh_token}, timeout=self._timeout)
                result["revoked"] = response.status_code == 200
            except httpx.TransportError:
                result["revoked"] = False
        result["deleted_file"] = self.store.clear()
        self._creds = None
        self._access_token = None
        self._expires_at = 0.0
        return result


def find_free_port() -> int:  # pragma: no cover - utility for manual tests
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((_LOOPBACK_HOST, 0))
        return s.getsockname()[1]
