from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agl.user_settings import load_user_settings, reset_ui_mode, save_user_settings, set_ui_mode
from agl.version import __version__
from agl.config import load_config
from ui.pages import CONSOLE_PAGE, PLAYER_PAGE, PORTAL_PAGE, SETTINGS_PAGE, WORKSPACE_PAGE

from .player import create_player_app
from .security import add_local_security


def _scoped_page(page: str, prefix: str) -> str:
    """Make legacy absolute /api calls work inside a mounted FastAPI app."""
    script = f"""<script>
(function(){{
  const prefix={json.dumps(prefix)};
  const nativeFetch=window.fetch.bind(window);
  window.fetch=function(resource, options){{
    if(typeof resource==='string' && resource.startsWith('/api/')) resource=prefix+resource;
    return nativeFetch(resource, options);
  }};
}})();
</script>"""
    return page.replace("<head>", "<head>" + script, 1)


class ModeRequest(BaseModel):
    mode: str


class EmbeddedModeNavigator:
    URLS = {
        "simple": "/player/",
        "advanced": "/workspace/",
        "settings": "/settings/",
    }

    def start(self, mode: str) -> str:
        try:
            return self.URLS[mode]
        except KeyError as exc:
            raise ValueError(f"Unknown mode: {mode}") from exc


class EmbeddedConsoleManager:
    """Mount project consoles into the one desktop web application."""

    def __init__(self, root_app: FastAPI):
        self.root_app = root_app
        self._lock = threading.RLock()
        self.consoles: Dict[str, Dict[str, Any]] = {}

    def start(self, project_name: str, project_dir: Path | str) -> str:
        from .project_console import create_app as create_console_app

        resolved = Path(project_dir).resolve()
        identity = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        prefix = f"/console/{identity}"
        with self._lock:
            if identity not in self.consoles:
                console = create_console_app(
                    resolved,
                    page=_scoped_page(CONSOLE_PAGE, prefix),
                    portal_url="/",
                )
                self.root_app.mount(prefix, console, name=f"console-{identity}")
                self.consoles[identity] = {
                    "project": project_name,
                    "project_dir": str(resolved),
                    "url": prefix + "/",
                }
        return prefix + "/"

    def list(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [dict(item, running=True) for item in self.consoles.values()]

    def stop_all(self) -> None:
        # Mounted apps share the root server lifecycle; there are no child
        # Uvicorn threads to stop.
        self.consoles.clear()


def create_desktop_app() -> FastAPI:
    """Create the canonical single-server desktop application."""
    from .settings import create_settings_app
    from .workspace import create_workspace_app

    app = FastAPI(title="AutoGame Localizer Desktop")
    add_local_security(app)
    navigator = EmbeddedModeNavigator()

    @app.get("/", response_class=HTMLResponse)
    def portal() -> str:
        return PORTAL_PAGE

    @app.get("/api/settings")
    def settings():
        return load_user_settings()

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "runtime": "desktop-single-server",
        }

    @app.get("/api/readiness")
    def readiness():
        config = load_config()
        provider = config.first_available_provider()
        selected = config.get_provider(provider) if provider != "mock" else None
        return {
            "provider_ready": provider != "mock",
            "provider": None if provider == "mock" else provider,
            "provider_mode": (
                "local" if selected and selected.is_local_endpoint
                else "cloud" if selected
                else None
            ),
            "supported_engines": ["RPG Maker MV/MZ", "Ren'Py", "Unity text assets"],
        }

    @app.post("/api/choose_mode")
    def choose_mode(body: ModeRequest):
        if body.mode not in {"simple", "advanced"}:
            raise HTTPException(status_code=400, detail="Invalid mode.")
        set_ui_mode(body.mode)
        return {"url": navigator.start(body.mode)}

    @app.post("/api/open_mode")
    def open_mode(body: ModeRequest):
        if body.mode not in navigator.URLS:
            raise HTTPException(status_code=400, detail="Invalid mode.")
        if body.mode in {"simple", "advanced"}:
            state = load_user_settings()
            state.update(ui_mode=body.mode, first_run_completed=True)
            save_user_settings(state)
        return {"url": navigator.start(body.mode)}

    @app.post("/api/reset_mode")
    def reset_mode():
        reset_ui_mode()
        return {"ok": True}

    console_manager = EmbeddedConsoleManager(app)
    app.mount(
        "/player",
        create_player_app(_scoped_page(PLAYER_PAGE, "/player"), portal_url="/"),
        name="player",
    )
    app.mount(
        "/workspace",
        create_workspace_app(
            console_manager=console_manager,
            page=_scoped_page(WORKSPACE_PAGE, "/workspace"),
            portal_url="/",
        ),
        name="workspace",
    )
    app.mount(
        "/settings",
        create_settings_app(
            page=_scoped_page(SETTINGS_PAGE, "/settings"),
            portal_url="/",
        ),
        name="settings",
    )
    return app
