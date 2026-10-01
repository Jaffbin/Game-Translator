from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Union


def app_root() -> Path:
    """
    Writable application data directory.

    When running from source:
      current working directory

    Packaged Windows builds share %LOCALAPPDATA%/AutoGameLocalizer so projects,
    settings and translation memory survive replacing or moving the EXE.
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
            return base / "AutoGameLocalizer"
        return Path(sys.executable).resolve().parent

    return Path.cwd()


def secrets_env_path() -> Path:
    """Keep the .env fallback stable across rebuilt Windows EXE folders."""
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        folder = app_root()
        folder.mkdir(parents=True, exist_ok=True)
        return folder / ".env"
    return app_root() / ".env"


def _get_root(env_name: str, default: str) -> Path:
    env_value = os.getenv(env_name)

    if env_value:
        root = Path(env_value).expanduser().resolve()
    else:
        root = app_root() / default

    root.mkdir(parents=True, exist_ok=True)
    return root


def projects_root() -> Path:
    """
    Root folder for all translation projects.

    Default:

      <app_root>/projects/
    """
    return _get_root("AGL_PROJECTS_ROOT", "projects")


def patches_root() -> Path:
    """
    Optional global patches folder.

    Default:

      <app_root>/patches/
    """
    return _get_root("AGL_PATCHES_ROOT", "patches")


def ensure_workspace() -> None:
    """
    Ensure default workspace folders exist.
    """
    projects_root()
    patches_root()


def sanitize_project_name(name: str) -> str:
    """
    Convert user input into a safe folder name.
    """
    name = (name or "").strip()

    if not name:
        return "NewProject"

    name = re.sub(
        r"[^\w\-\.\u4e00-\u9fff]+",
        "_",
        name,
        flags=re.UNICODE,
    )

    return name or "NewProject"


def project_path(project_name: str) -> Path:
    """
    Return:

      projects/<project_name>
    """
    return projects_root() / sanitize_project_name(project_name)


def project_patches_root(project_dir: Union[str, Path]) -> Path:
    """
    Return:

      projects/<project>/patches
    """
    path = Path(project_dir) / "patches"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_exports_root(project_dir: Union[str, Path]) -> Path:
    """
    Return:

      projects/<project>/exports
    """
    path = Path(project_dir) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_reports_root(project_dir: Union[str, Path]) -> Path:
    """
    Return:

      projects/<project>/reports
    """
    path = Path(project_dir) / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path
