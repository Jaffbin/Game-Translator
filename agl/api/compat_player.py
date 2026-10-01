"""Compatibility adapter for the former one-click player API.

The canonical player separates local preparation from cloud translation.
Existing callers of ``/api/start`` retain one-click behavior through this
adapter while sharing the current workflow implementation and safety gates.
"""

from __future__ import annotations

import argparse
import os
import threading
import time

import uvicorn
from fastapi import HTTPException

from ui.pages import PLAYER_PAGE

from ..services import PlayerWorkflow
from .player import GameRequest, create_player_app, select_folder_native


PAGE = PLAYER_PAGE


def create_simple_app():
    workflow = PlayerWorkflow()
    portal_url = os.environ.get("AGL_PORTAL_URL", "http://127.0.0.1:8300/")
    app = create_player_app(PAGE, portal_url=portal_url, workflow=workflow)

    @app.post("/api/start")
    def start_legacy(body: GameRequest):
        try:
            workflow.prepare(body.game_path, body.target_language)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        def continue_after_scan() -> None:
            while workflow.snapshot()["state"] == "preparing":
                time.sleep(0.05)
            if workflow.snapshot()["state"] == "prepared":
                try:
                    workflow.translate()
                except Exception:
                    return

        threading.Thread(target=continue_after_scan, daemon=True).start()
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoGame Localizer player compatibility API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    args = parser.parse_args()
    uvicorn.run(create_simple_app(), host=args.host, port=args.port)


__all__ = [
    "PAGE",
    "GameRequest",
    "create_simple_app",
    "main",
    "select_folder_native",
]
