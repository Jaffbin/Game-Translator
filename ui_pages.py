"""Backward-compatible shim for the unified UI pages.

New code should import from ``ui.pages``.  This module is intentionally kept so
existing tests and third-party scripts do not break during the directory refactor.
"""
from ui.pages import (  # noqa: F401
    CONSOLE_PAGE,
    PLAYER_PAGE,
    PORTAL_PAGE,
    SETTINGS_PAGE,
    WORKSPACE_PAGE,
)
