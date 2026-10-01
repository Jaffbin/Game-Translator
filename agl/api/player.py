from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..services import PlayerWorkflow
from ..services.diagnostics import diagnose_game
from ..config import load_config
from ..windows_folder_picker import choose_game_folder
from .security import add_local_security


class GameRequest(BaseModel):
    game_path: str
    target_language: str = "zh-CN"


def select_folder_native() -> str:
    return choose_game_folder()


def create_player_app(
    page: str,
    portal_url: str = "/",
    workflow: Optional[PlayerWorkflow] = None,
) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Player")
    add_local_security(app)
    workflow = workflow or PlayerWorkflow()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page.replace("__PORTAL_URL__", portal_url)

    @app.post("/api/select_folder")
    def select_folder():
        try:
            return {"path": select_folder_native()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/preflight")
    def preflight(body: GameRequest):
        try:
            return workflow.preflight(Path(body.game_path), body.target_language)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/diagnostics")
    def diagnostics(body: GameRequest):
        try:
            return diagnose_game(body.game_path, body.target_language)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/recovery")
    def recovery(body: GameRequest):
        try:
            return workflow.recovery(body.game_path, body.target_language)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/state")
    def state():
        return workflow.snapshot()

    @app.get("/api/preferences")
    def preferences():
        config = load_config()
        return {"target_language": config.target_language}

    @app.post("/api/prepare")
    def prepare(body: GameRequest):
        try:
            workflow.prepare(body.game_path, body.target_language)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/auto")
    def auto(body: GameRequest):
        try:
            workflow.start_auto(body.game_path, body.target_language)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/translate")
    def translate():
        try:
            workflow.translate()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/qa")
    def rerun_qa():
        try:
            workflow.rerun_qa()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/cancel")
    def cancel():
        try:
            workflow.cancel()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/apply")
    def apply():
        try:
            workflow.apply()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/restore")
    def restore():
        try:
            workflow.restore()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app
