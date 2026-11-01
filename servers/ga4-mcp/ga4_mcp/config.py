"""Static configuration for ga4-mcp (no secrets here — see .env.example)."""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__

APP_NAME = "ga4-mcp"
VERSION = __version__

DATA_API_BASE = "https://analyticsdata.googleapis.com"
ADMIN_API_BASE = "https://analyticsadmin.googleapis.com"
DATA_API_VERSION = "v1beta"
ADMIN_API_VERSION = "v1beta"
ADMIN_API_ALPHA_VERSION = "v1alpha"  # only for resources that have no v1beta read

ALLOWED_HOSTS = frozenset({"analyticsdata.googleapis.com", "analyticsadmin.googleapis.com"})

# Phase 1 is read-only: one scope only (PROJECT.md §6).
SCOPES_READONLY = ["https://www.googleapis.com/auth/analytics.readonly"]

# Output / request limits (PROJECT.md §5).
OUTPUT_BUDGET_BYTES = 50_000
DEFAULT_REPORT_LIMIT = 100
MAX_REPORT_LIMIT = 1_000
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_BATCH_REPORTS = 5
DEFAULT_START_DATE = "7daysAgo"
DEFAULT_END_DATE = "yesterday"

ENV_CONFIG_DIR = "GA4_MCP_CONFIG_DIR"
ENV_AUDIT_DISABLED = "GA4_MCP_AUDIT_DISABLED"


def config_dir() -> Path:
    """``~/.ga4-mcp`` unless ``GA4_MCP_CONFIG_DIR`` points elsewhere."""
    override = os.environ.get(ENV_CONFIG_DIR)
    return Path(override).expanduser() if override else Path.home() / ".ga4-mcp"


def audit_enabled() -> bool:
    return os.environ.get(ENV_AUDIT_DISABLED, "").strip().lower() not in {"1", "true", "yes"}
