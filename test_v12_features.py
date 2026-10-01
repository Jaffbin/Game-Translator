from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agl.cache import TranslationMemory
from agl.config import AppConfig, ProviderConfig
from agl.models import EntryStatus, TranslationEntry
from agl import operations
from agl.project import ProjectStore
from fastapi.testclient import TestClient
import phase75_web


class CaptureProvider:
    id = "capture"
    model = "capture-model"

    def __init__(self):
        self.glossary = None
        self.context = None

    def translate(self, text, target_language, context="", glossary=None):
        self.context = context
        self.glossary = glossary
        return "译文"


def make_project(tmp: Path) -> Path:
    project = tmp / "P"
    project.mkdir()
    store = ProjectStore.create(project, tmp / "Game", "rpgmaker_mv_mz", "zh-CN", "P")
    store.upsert_entries([
        TranslationEntry(
            id="entry-1",
            source_text="Dragonborn entered the guild.",
            context="A fantasy NPC greeting.",
            file_path="Data/Common.json",
            engine="rpgmaker_mv_mz",
            status=EntryStatus.PENDING,
        )
    ])
    store.save_meta()
    store.close()
    return project


def test_glossary_and_memory_and_suggestion_api():
    with TemporaryDirectory() as td:
        root = Path(td)
        project = make_project(root)
        app = phase75_web.create_app(project)
        with TestClient(app) as client:
            assert client.get("/api/glossary").status_code == 200
            assert client.post("/api/glossary", json={
                "source_term": "Dragonborn",
                "target_term": "龙裔",
                "level": "required",
            }).status_code == 200
            glossary = client.get("/api/glossary?q=dragon").json()["items"]
            assert glossary[0]["target_term"] == "龙裔"
            assert client.delete("/api/glossary", params={"source_term": "Dragonborn"}).status_code == 200

        cache_db = root / "tm.db"
        with TranslationMemory(cache_db) as tm:
            tm.put("Hello", "zh-CN", "你好", provider="mock", model="mock", human_reviewed=True)
            rows = tm.search("hello", "zh-CN")
            assert rows and rows[0]["translated_text"] == "你好"


def test_batch_translation_passes_project_glossary(monkeypatch):
    with TemporaryDirectory() as td:
        root = Path(td)
        project = make_project(root)
        glossary_path = project / "glossary.csv"
        with glossary_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["source_term", "target_term", "level", "case_sensitive"])
            writer.writeheader()
            writer.writerow({"source_term": "Dragonborn", "target_term": "龙裔", "level": "required", "case_sensitive": "false"})

        cfg = AppConfig(
            target_language="zh-CN",
            cache_db=root / "cache.db",
            providers={"capture": ProviderConfig(id="capture", type="mock", model="capture-model")},
        )
        provider = CaptureProvider()
        monkeypatch.setattr(operations, "load_config", lambda: cfg)
        monkeypatch.setattr(operations, "create_provider", lambda config, model_override=None: provider)

        result = operations.translate_project(project, provider_id="capture")
        assert result["translated"] == 1
        assert provider.glossary == {"Dragonborn": "龙裔"}
