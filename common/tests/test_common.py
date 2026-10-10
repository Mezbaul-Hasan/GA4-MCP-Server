"""Unit tests for the shared helpers (no network)."""

from __future__ import annotations

import json
import os
import stat
import sys

import httpx
import pytest
import respx

from marketing_mcp_common.audit import AuditLog
from marketing_mcp_common.errors import AuthError, PlatformHTTPError, google_error_to_tool_error
from marketing_mcp_common.google_auth import (
    GOOGLE_TOKEN_URL,
    GoogleCredentialStore,
    GoogleCredentials,
    GoogleTokenProvider,
    build_auth_url,
    credentials_from_token_response,
    make_pkce_pair,
)
from marketing_mcp_common.http import HttpClient, HostNotAllowedError, parse_retry_after
from marketing_mcp_common.logging import get_logger
from marketing_mcp_common.pagination import enforce_output_budget, json_size, parse_number, validate_page_size
from marketing_mcp_common.redact import redact, redact_mapping

pytestmark = pytest.mark.anyio


# ----------------------------------------------------------------------------- redact / logging
def test_redact_tokens_and_secrets():
    text = (
        "Authorization: Bearer ya29.a0AfH6SMBxyz-123 refresh=1//0gABCDEFGHIJKLMNOPQRSTUVWXYZ "
        'client_secret=GOCSPX-abc123 {"refresh_token": "1//0gZZZZZZZZZZZZZZZZZZZZZZZZZ", "access_token": "ya29.other"}'
    )
    out = redact(text)
    for secret in ("ya29.a0AfH6SMBxyz-123", "1//0gABCDEFGHIJKLMNOPQRSTUVWXYZ", "GOCSPX-abc123", "ya29.other", "1//0gZZZZZZZZZZZZZZZZZZZZZZZZZ"):
        assert secret not in out
    assert "[REDACTED]" in out


def test_redact_mapping_by_key_and_length():
    data = {"client_secret": "x", "nested": {"refresh_token": "y", "ok": "fine", "long": "a" * 300}, "list": [{"api_key": "z"}]}
    out = redact_mapping(data)
    assert out["client_secret"] == "[REDACTED]" and out["nested"]["refresh_token"] == "[REDACTED]"
    assert out["nested"]["ok"] == "fine" and len(out["nested"]["long"]) == 200
    assert out["list"][0]["api_key"] == "[REDACTED]"


def test_logger_writes_to_stderr_and_redacts(capsys):
    log = get_logger("test.redact")
    log.info("token ya29.secret-token-value here")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ya29.secret-token-value" not in captured.err and "ya29.[REDACTED]" in captured.err


# ----------------------------------------------------------------------------- pagination / budget
def test_validate_page_size_and_budget():
    assert validate_page_size(None, default=50) == 50
    with pytest.raises(Exception):
        validate_page_size(0)
    with pytest.raises(Exception):
        validate_page_size(10_000)
    rows = [{"i": i, "pad": "x" * 100} for i in range(3000)]
    result = enforce_output_budget({"rows": rows, "other": 1}, "rows", budget=20_000)
    assert result["truncated"] is True and len(result["rows"]) < 3000 and json_size(result) <= 20_000
    small = enforce_output_budget({"rows": rows[:5]}, "rows", budget=20_000)
    assert small["truncated"] is False and len(small["rows"]) == 5
    assert parse_number("12") == 12 and parse_number("1.5") == 1.5 and parse_number("n/a") == "n/a"


# ----------------------------------------------------------------------------- errors
def test_google_error_to_tool_error_includes_hint_and_details():
    err = PlatformHTTPError(
        400,
        "bad",
        code="INVALID_ARGUMENT",
        body={"error": {"code": 400, "message": "Field x is not valid.", "status": "INVALID_ARGUMENT",
                        "details": [{"@type": "type.googleapis.com/google.rpc.BadRequest", "fieldViolations": [{"field": "metrics", "description": "unknown"}]}]}},
        request_id="rid-1",
    )
    tool_error = google_error_to_tool_error(err, platform="GA4", hints={400: "fix the names"})
    text = str(tool_error)
    assert text.startswith("GA4 API 400 (INVALID_ARGUMENT): Field x is not valid. [metrics: unknown] (request-id rid-1) — fix the names")


# ----------------------------------------------------------------------------- http
def test_parse_retry_after():
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # past date → 0


async def test_http_client_host_allow_list_and_retry():
    class Provider:
        async def get_access_token(self, *, force_refresh: bool = False) -> str:
            return "tok"

    client = HttpClient(token_provider=Provider(), allowed_hosts={"api.example.com"}, backoff_base=0, backoff_cap=0, max_retries=2)
    with pytest.raises(HostNotAllowedError):
        await client.get_json("https://other.example.com/x")
    with pytest.raises(HostNotAllowedError):
        await client.get_json("http://api.example.com/x")
    with respx.mock(assert_all_mocked=True) as router:
        route = router.get("https://api.example.com/x")
        route.side_effect = [httpx.Response(500, json={"error": {"message": "boom"}}), httpx.Response(200, json={"ok": True})]
        assert await client.get_json("https://api.example.com/x") == {"ok": True}
        assert route.calls.last.request.headers["authorization"] == "Bearer tok"
        route2 = router.get("https://api.example.com/fail").respond(404, json={"error": {"code": 404, "message": "nope", "status": "NOT_FOUND"}})
        with pytest.raises(PlatformHTTPError) as excinfo:
            await client.get_json("https://api.example.com/fail?secret=1")
        assert excinfo.value.status_code == 404 and excinfo.value.code == "NOT_FOUND"
        assert "secret" not in excinfo.value.path and route2.called
    await client.aclose()


# ----------------------------------------------------------------------------- google auth
def test_pkce_and_auth_url():
    verifier, challenge = make_pkce_pair()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge
    url = build_auth_url(client_id="cid", redirect_uri="http://127.0.0.1:1234/callback", scopes=["a", "b"], state="st", code_challenge=challenge)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    for part in ("client_id=cid", "access_type=offline", "prompt=consent", "code_challenge_method=S256", "state=st", "scope=a+b"):
        assert part in url


def test_credential_store_file_permissions_and_env_override(tmp_path, monkeypatch):
    store = GoogleCredentialStore(tmp_path / "cfg", env_prefix="GOOGLE")
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert store.load() is None
    creds = GoogleCredentials(client_id="cid", client_secret="sec", refresh_token="1//rt", scopes=["s"])
    path = store.save(creds)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    loaded = store.load()
    assert loaded and loaded.refresh_token == "1//rt" and loaded.source == "file"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "env-rt")
    env_creds = store.load()
    assert env_creds and env_creds.source == "env" and env_creds.client_id == "env-id"
    assert store.clear() is True and store.clear() is False


def test_credentials_from_token_response_marks_testing_status_expiry():
    creds = credentials_from_token_response(
        {"access_token": "a", "refresh_token": "r", "scope": "x y", "refresh_token_expires_in": 604799},
        client_id="c", client_secret="s", requested_scopes=["x"],
    )
    assert creds.scopes == ["x", "y"] and creds.refresh_token_expires_at
    assert creds.refresh_token_days_left() in (6, 7)
    with pytest.raises(AuthError):
        credentials_from_token_response({"access_token": "a"}, client_id="c", client_secret="s", requested_scopes=[])


async def test_token_provider_refresh_cache_and_invalid_grant(tmp_path):
    store = GoogleCredentialStore(tmp_path, env_prefix="NOPE")
    store.save(GoogleCredentials(client_id="cid", client_secret="sec", refresh_token="1//rt", refresh_token_expires_at="2026-10-17T00:00:00+00:00"))
    provider = GoogleTokenProvider(store, app_name="ga4-mcp")
    with respx.mock(assert_all_mocked=True) as router:
        route = router.post(GOOGLE_TOKEN_URL).respond(json={"access_token": "ya29.new", "expires_in": 3600, "token_type": "Bearer"})
        assert await provider.get_access_token() == "ya29.new"
        assert await provider.get_access_token() == "ya29.new"  # cached
        assert route.call_count == 1
        sent = dict(pair.split("=") for pair in route.calls.last.request.content.decode().split("&"))
        assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "1%2F%2Frt"
        route.respond(status_code=400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        with pytest.raises(AuthError) as excinfo:
            await provider.get_access_token(force_refresh=True)
        msg = str(excinfo.value)
        assert "invalid_grant" in msg and "ga4-mcp auth" in msg and "Testing" in msg
    status = provider.status()
    assert status["signed_in"] is True and status["client_id"].startswith("cid") and "warning" in status
    assert "sec" not in json.dumps(status) or status["client_id"] == "cid"  # secret never in status


def test_token_provider_without_credentials_explains_next_step(tmp_path, monkeypatch):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    provider = GoogleTokenProvider(GoogleCredentialStore(tmp_path / "none"), app_name="ga4-mcp")
    with pytest.raises(AuthError) as excinfo:
        provider.credentials()
    assert "ga4-mcp auth" in str(excinfo.value)
    assert provider.status()["signed_in"] is False


# ----------------------------------------------------------------------------- audit
def test_audit_log_redacts_and_rotates(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=200)
    log.record(tool="t", args={"property_id": "1", "client_secret": "s3cr3t", "note": "Bearer ya29.abc"}, status="ok", duration_ms=1.234, meta={"rows": 2})
    text = (tmp_path / "audit.jsonl").read_text()
    entry = json.loads(text.strip())
    assert entry["args"]["client_secret"] == "[REDACTED]" and "ya29.abc" not in text and entry["meta"] == {"rows": 2}
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "audit.jsonl").st_mode) == 0o600
    for _ in range(5):
        log.record(tool="t", args={}, status="ok", duration_ms=1)
    assert (tmp_path / "audit.jsonl.1").exists()
