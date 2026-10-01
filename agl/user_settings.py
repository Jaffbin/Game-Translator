from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .workspace import app_root
from .io_utils import atomic_write_text


DEFAULT_SETTINGS: Dict[str, Any] = {
    "ui_mode": "",
    "first_run_completed": False,
    "auto_open_last_mode": False,
}


def user_settings_path() -> Path:
    return app_root() / "user_settings.json"


def load_user_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)

    path = user_settings_path()

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                settings.update(data)
        except Exception:
            pass

    return settings


def save_user_settings(settings: Dict[str, Any]) -> None:
    merged = load_user_settings()
    merged.update(settings)

    path = user_settings_path()
    atomic_write_text(
        path,
        json.dumps(merged, ensure_ascii=False, indent=2),
    )


def set_ui_mode(mode: str) -> Dict[str, Any]:
    if mode not in {"simple", "advanced"}:
        raise ValueError(f"Invalid UI mode: {mode}")

    settings = load_user_settings()
    settings["ui_mode"] = mode
    settings["first_run_completed"] = True

    save_user_settings(settings)

    return settings


def reset_ui_mode() -> Dict[str, Any]:
    settings = load_user_settings()
    settings["ui_mode"] = ""
    settings["first_run_completed"] = False

    save_user_settings(settings)

    return settings
