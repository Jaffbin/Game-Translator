from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import phase75_web
from agl.models import EntryStatus, TranslationEntry
from agl.cache import TranslationMemory
from agl.config import AppConfig
from agl import operations
from agl.project import ProjectStore


def make_project(tmp: Path) -> Path:
    project = tmp / "P"
    project.mkdir()
    store = ProjectStore.create(project, tmp / "Game", "rpgmaker_mv_mz", "zh-CN", "P")
    store.upsert_entries([
        TranslationEntry(id="e1", source_text="Hello", target_text="你好", file_path="Data/A.json", engine="rpgmaker_mv_mz", status=EntryStatus.MACHINE_TRANSLATED),
        TranslationEntry(id="e2", source_text="Open the gate", target_text="", file_path="Data/A.json", engine="rpgmaker_mv_mz", status=EntryStatus.NEEDS_REVIEW, note="[WARN:GLOSSARY] 缺少术语：gate"),
        TranslationEntry(id="e3", source_text="Broken {player}", target_text="坏的", file_path="Data/B.json", engine="rpgmaker_mv_mz", status=EntryStatus.ERROR, note="[ERROR:PLACEHOLDER] placeholder mismatch"),
    ])
    store.save_meta()
    store.close()
    return project


def test_bulk_update_and_qa_issue_listing(monkeypatch):
    with TemporaryDirectory() as td:
        root = Path(td)
        project = make_project(root)
        cache_db = root / "reviewed-memory.db"
        monkeypatch.setattr(
            operations,
            "load_config",
            lambda: AppConfig(target_language="zh-CN", cache_db=cache_db),
        )
        app = phase75_web.create_app(project)
        with TestClient(app) as client:
            r = client.post("/api/entries/bulk-update", json={"entry_ids": ["e1", "e2"], "action": "reviewed"})
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 1

            r = client.post("/api/entries/bulk-update", json={"entry_ids": ["e1", "e2"], "action": "lock"})
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 2

            issues = client.get("/api/qa/issues").json()
            assert issues["total"] == 1
            assert issues["items"][0]["entry_id"] == "e3"
            assert "placeholder" in issues["items"][0]["note"].lower()

            bad = client.post("/api/entries/bulk-update", json={"entry_ids": ["e1"], "action": "delete"})
            assert bad.status_code == 400

        with TranslationMemory(cache_db) as memory:
            reviewed = memory.get_exact_any("Hello", "zh-CN")
        assert reviewed is not None
        assert reviewed["translated_text"] == "你好"
        assert reviewed["human_reviewed"] == 1
