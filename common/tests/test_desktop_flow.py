"""End-to-end test of the loopback OAuth flow without Google: we play the browser + token endpoint."""

from __future__ import annotations

import re
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest
import respx

from marketing_mcp_common.errors import AuthError
from marketing_mcp_common.google_auth import GOOGLE_TOKEN_URL, GoogleOAuthDesktopFlow


def _run_flow(flow: GoogleOAuthDesktopFlow, state_override: str | None = None, error: str | None = None):
    printed: list[str] = []
    result: dict = {}

    def runner():
        try:
            result["creds"] = flow.run(echo=printed.append)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    t = threading.Thread(target=runner)
    t.start()
    # Wait (up to 30 s — slow CI runners) for the URL to be printed, then act as the browser.
    for _ in range(1500):
        if printed:
            break
        threading.Event().wait(0.02)
    assert printed, "flow did not print the sign-in URL within 30 s"
    url = re.search(r"https://accounts\.google\.com\S+", printed[0]).group(0)
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    callback = q["redirect_uri"]
    state = state_override or q["state"]
    cb_url = f"{callback}?error={error}&state={state}" if error else f"{callback}?code=the-code&state={state}"
    try:
        with urllib.request.urlopen(cb_url, timeout=5) as resp:
            page = resp.read().decode()
    except urllib.error.HTTPError as exc:  # failure page returns 400
        page = exc.read().decode()
    t.join(timeout=10)
    return q, result, page


def test_desktop_flow_happy_path():
    flow = GoogleOAuthDesktopFlow(app_name="ga4-mcp", client_id="cid", client_secret="sec", scopes=["https://www.googleapis.com/auth/analytics.readonly"], open_browser=False, timeout=10)
    with respx.mock(assert_all_mocked=True) as router:
        token_route = router.post(GOOGLE_TOKEN_URL).respond(json={"access_token": "ya29.x", "refresh_token": "1//rt", "expires_in": 3599, "scope": "https://www.googleapis.com/auth/analytics.readonly"})
        q, result, page = _run_flow(flow)
    assert "connected" in page and "creds" in result, result
    creds = result["creds"]
    assert creds.refresh_token == "1//rt" and creds.scopes == ["https://www.googleapis.com/auth/analytics.readonly"]
    assert q["code_challenge_method"] == "S256" and q["access_type"] == "offline" and q["prompt"] == "consent"
    body = dict(pair.split("=") for pair in token_route.calls.last.request.content.decode().split("&"))
    assert body["grant_type"] == "authorization_code" and body["code"] == "the-code" and "code_verifier" in body
    assert body["redirect_uri"].startswith("http%3A%2F%2F127.0.0.1%3A")


def test_desktop_flow_rejects_bad_state_and_denied():
    flow = GoogleOAuthDesktopFlow(app_name="ga4-mcp", client_id="cid", client_secret="sec", scopes=["s"], open_browser=False, timeout=10)
    _, result, _ = _run_flow(flow, state_override="wrong-state")
    assert isinstance(result.get("error"), AuthError) and "state mismatch" in str(result["error"])

    flow = GoogleOAuthDesktopFlow(app_name="ga4-mcp", client_id="cid", client_secret="sec", scopes=["s"], open_browser=False, timeout=10)
    _, result, page = _run_flow(flow, error="access_denied")
    assert isinstance(result.get("error"), AuthError) and "access_denied" in str(result["error"])
    assert "failed" in page.lower()


def test_desktop_flow_times_out():
    flow = GoogleOAuthDesktopFlow(app_name="ga4-mcp", client_id="cid", client_secret="sec", scopes=["s"], open_browser=False, timeout=1.2)
    with pytest.raises(AuthError, match="timed out"):
        flow.run(echo=lambda *_: None)
