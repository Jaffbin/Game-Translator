from __future__ import annotations

import sys
from agl.api import project_console as canonical_console
from agl.api import settings as canonical_settings
from agl.api import workspace as canonical_workspace
from .pages import CONSOLE_PAGE, PLAYER_PAGE, PORTAL_PAGE, SETTINGS_PAGE, WORKSPACE_PAGE


def install() -> None:
    """Install templates without introducing new dependencies on phase modules."""
    canonical_workspace.PAGE = WORKSPACE_PAGE
    canonical_settings.PAGE = SETTINGS_PAGE
    canonical_console.PAGE = CONSOLE_PAGE
    pages = {
        "phase_ui3_launcher": ("PORTAL_PAGE", PORTAL_PAGE),
        "phase_ui1_simple": ("PAGE", PLAYER_PAGE),
        "phase11_workspace": ("PAGE", WORKSPACE_PAGE),
        "phase12_settings": ("PAGE", SETTINGS_PAGE),
        "phase75_web": ("PAGE", CONSOLE_PAGE),
    }
    for module_name, (attribute, value) in pages.items():
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, attribute, value)
