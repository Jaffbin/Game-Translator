"""Compatibility surface for callers of the former multi-service launcher.

The production desktop uses :mod:`agl.api.desktop` and one HTTP server. This
module only supports external v19 integrations during the v20 deprecation
window.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Dict, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ui.pages import PLAYER_PAGE, PORTAL_PAGE, SETTINGS_PAGE, WORKSPACE_PAGE

from agl.user_settings import (
    load_user_settings,
    reset_ui_mode,
    save_user_settings,
    set_ui_mode,
)

from .launcher import main
from .player import create_player_app
from .security import add_local_security
from .settings import create_settings_app
from .workspace import create_workspace_app


MODE_DEFAULT_PORTS = {"simple": 8310, "advanced": 8320, "settings": 8330}


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start_port: int, max_attempts: int = 200) -> int:
    for port in range(start_port, start_port + max_attempts):
        if not is_port_open(port):
            return port
    raise RuntimeError(f"No free port found near {start_port}.")


def resolve_port(preferred_port: int) -> int:
    return preferred_port if not is_port_open(preferred_port) else find_free_port(preferred_port + 1)


def wait_for_port(port: int, timeout_seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.05)
    return False


def wait_for_server(
    server: uvicorn.Server,
    thread: threading.Thread,
    timeout_seconds: float = 15.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return True
        if not thread.is_alive() or getattr(server, "should_exit", False):
            return False
        time.sleep(0.05)
    return False


class ServiceManager:
    """Deprecated standalone-mode manager retained for external integrations."""

    def __init__(self, portal_url: str):
        self.portal_url = portal_url
        self.services: Dict[str, Dict[str, Any]] = {}

    def _create_app(self, mode: str) -> Tuple[FastAPI, str]:
        if mode == "simple":
            return create_player_app(PLAYER_PAGE, portal_url=self.portal_url), "/"
        if mode == "advanced":
            return create_workspace_app(page=WORKSPACE_PAGE, portal_url=self.portal_url), "/"
        if mode == "settings":
            return create_settings_app(page=SETTINGS_PAGE, portal_url=self.portal_url), "/"
        raise ValueError(f"Unknown mode: {mode}")

    def start(self, mode: str) -> str:
        existing = self.services.get(mode)
        if existing is not None:
            server = existing["server"]
            thread = existing["thread"]
            if thread.is_alive() and not getattr(server, "should_exit", False):
                return existing["url"]

        app, suffix = self._create_app(mode)
        port = resolve_port(MODE_DEFAULT_PORTS.get(mode, 8400))
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        if not wait_for_server(server, thread):
            server.should_exit = True
            thread.join(timeout=1)
            raise RuntimeError(f"Failed to start compatibility service: {mode}")

        url = f"http://127.0.0.1:{port}{suffix}"
        self.services[mode] = {
            "server": server,
            "thread": thread,
            "port": port,
            "url": url,
        }
        return url

    def stop_all(self) -> None:
        for info in self.services.values():
            info["server"].should_exit = True
        for info in self.services.values():
            info["thread"].join(timeout=3)
        self.services.clear()


class ModeRequest(BaseModel):
    mode: str


def create_portal_app(services: ServiceManager) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer compatibility launcher")
    add_local_security(app)

    @app.get("/", response_class=HTMLResponse)
    def portal_page() -> str:
        return PORTAL_PAGE

    @app.get("/api/settings")
    def settings():
        return load_user_settings()

    @app.post("/api/choose_mode")
    def choose_mode(body: ModeRequest):
        if body.mode not in {"simple", "advanced"}:
            raise HTTPException(status_code=400, detail="Invalid mode.")
        set_ui_mode(body.mode)
        return {"url": services.start(body.mode)}

    @app.post("/api/open_mode")
    def open_mode(body: ModeRequest):
        if body.mode not in MODE_DEFAULT_PORTS:
            raise HTTPException(status_code=400, detail="Invalid mode.")
        if body.mode in {"simple", "advanced"}:
            state = load_user_settings()
            state.update(ui_mode=body.mode, first_run_completed=True)
            save_user_settings(state)
        return {"url": services.start(body.mode)}

    @app.post("/api/reset_mode")
    def reset_mode():
        reset_ui_mode()
        return {"ok": True}

    return app


__all__ = [
    "MODE_DEFAULT_PORTS",
    "PORTAL_PAGE",
    "ServiceManager",
    "create_portal_app",
    "find_free_port",
    "is_port_open",
    "main",
    "resolve_port",
    "wait_for_port",
    "wait_for_server",
]
