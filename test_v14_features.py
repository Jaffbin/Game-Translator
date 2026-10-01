from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import phase75_web
from agl.models import EntryStatus, TranslationEntry
from agl.project import ProjectStore


def make_project(tmp: Path) -> Path:
    project = tmp / "P"
    project.mkdir()
    store = ProjectStore.create(project, tmp / "Game", "rpgmaker_mv_mz", "zh-CN", "P")
    store.upsert_entries([
        TranslationEntry(id="e1", source_text="Open the gate", target_text="打开大门", context="Castle ending dialogue", file_path="Data/A.json", engine="rpgmaker_mv_mz", status=EntryStatus.REVIEWED),
        TranslationEntry(id="e2", source_text="The gate is open", target_text="大门已打开", context="Castle dialogue", file_path="Data/A.json", engine="rpgmaker_mv_mz", status=EntryStatus.MACHINE_TRANSLATED),
        TranslationEntry(id="e3", source_text="You cannot pass", target_text="你不能通过", context="Guard dialogue", file_path="Data/B.json", engine="rpgmaker_mv_mz", status=EntryStatus.MACHINE_TRANSLATED),
    ])
    store.save_meta()
    store.close()
    (project / "glossary.csv").write_text(
        "source_term,target_term,level,case_sensitive\n"
        "gate,城门,required,false\n",
        encoding="utf-8-sig",
    )
    return project


def test_assistant_context_contract():
    with TemporaryDirectory() as td:
        project = make_project(Path(td))
        app = phase75_web.create_app(project)
        with TestClient(app) as client:
            r = client.get("/api/entries/e1/assistant-context")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["entry"]["id"] == "e1"
            assert body["entry"]["context"] == "Castle ending dialogue"
            assert body["glossary"][0]["source_term"] == "gate"
            assert body["glossary"][0]["target_term"] == "城门"
            assert body["qa"]["passed"] is False
            assert any(x["code"] == "glossary_missing" for x in body["qa"]["issues"])
            assert body["neighbors"]
            assert any(x["relation"] == "next" and x["id"] == "e2" for x in body["neighbors"])
            assert body["translation_memory"]["matches"] == []


def test_assistant_context_missing_entry():
    with TemporaryDirectory() as td:
        project = make_project(Path(td))
        app = phase75_web.create_app(project)
        with TestClient(app) as client:
            r = client.get("/api/entries/missing/assistant-context")
            assert r.status_code == 404
