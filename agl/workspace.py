from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Union


def app_root() -> Path:
    """
    Application root directory.

    When running from source:
      current working directory

    When running as PyInstaller EXE:
      folder containing the EXE

    If EXE folder is not writable, fallback to:
      %USERPROFILE%/AutoGameLocalizer
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent

        try:
            test_file = exe_dir / ".agl_write_test"
            test_file.touch()
            test_file.unlink()
            return exe_dir
        except Exception:
            return Path.home() / "AutoGameLocalizer"

    return Path.cwd()


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