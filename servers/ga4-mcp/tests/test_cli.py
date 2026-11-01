"""CLI: help/status/logout without network; auth flow pieces are covered in common/tests."""

from __future__ import annotations

import json

from ga4_mcp.cli import main


def test_help_and_unknown_command(capsys):
    assert main(["help"]) == 0
    assert "ga4-mcp auth" in capsys.readouterr().err
    assert main(["--help"]) == 0
    assert main(["bogus"]) == 2


def test_status_when_not_signed_in(config_dir, monkeypatch, capsys):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert main(["status"]) == 1
    info = json.loads(capsys.readouterr().out)
    assert info["signed_in"] is False and info["next_step"] == "run `ga4-mcp auth`"
    assert info["config_file"].startswith(str(config_dir))


def test_logout_without_credentials(config_dir, monkeypatch, capsys):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert main(["logout"]) == 0
    assert json.loads(capsys.readouterr().out) == {"revoked": False, "deleted_file": False}


def test_auth_requires_client_credentials(config_dir, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr("getpass.getpass", lambda *_: "")
    assert main(["auth"]) == 2
