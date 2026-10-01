from __future__ import annotations

from types import SimpleNamespace
import time

import pytest
from fastapi.testclient import TestClient

from agl.api.project_console import create_app as create_console_app
from agl.project import ProjectStore
from agl.services import automatic_workflow


def test_auto_workflow_runs_scan_translate_qa_in_order(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        automatic_workflow, "load_config",
        lambda: SimpleNamespace(first_available_provider=lambda: "gemini", get_provider=lambda _: SimpleNamespace(is_ready=True, is_local_endpoint=False)),
    )
    monkeypatch.setattr(
        automatic_workflow, "scan_project",
        lambda **kwargs: calls.append("scan") or {"entries": 2, "errors": 0},
    )
    monkeypatch.setattr(
        automatic_workflow, "translate_project",
        lambda **kwargs: calls.append("translate") or {"translated": 2, "failed": 0},
    )
    monkeypatch.setattr(
        automatic_workflow, "run_qa_project",
        lambda **kwargs: calls.append("qa") or {"entries_with_errors": 0},
    )

    result = automatic_workflow.run_automatic_workflow(tmp_path, lambda message: None)

    assert calls == ["scan", "translate", "qa"]
    assert result["translation"]["translated"] == 2


def test_auto_workflow_never_uses_mock(monkeypatch, tmp_path):
    monkeypatch.setattr(
        automatic_workflow, "load_config",
        lambda: SimpleNamespace(first_available_provider=lambda: "mock"),
    )

    with pytest.raises(RuntimeError, match="Provider"):
        automatic_workflow.run_automatic_workflow(tmp_path, lambda message: None)


def test_auto_workflow_skips_qa_when_provider_stops_translation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        automatic_workflow, "load_config",
        lambda: SimpleNamespace(first_available_provider=lambda: "gemini", get_provider=lambda _: SimpleNamespace(is_ready=True, is_local_endpoint=False)),
    )
    monkeypatch.setattr(automatic_workflow, "scan_project", lambda **kwargs: {"entries": 2, "errors": 0})
    monkeypatch.setattr(
        automatic_workflow,
        "translate_project",
        lambda **kwargs: {"translated": 1, "failed": 1},
    )
    monkeypatch.setattr(
        automatic_workflow,
        "run_qa_project",
        lambda **kwargs: calls.append("qa") or {"entries_with_errors": 1},
    )

    with pytest.raises(RuntimeError, match="1 条失败"):
        automatic_workflow.run_automatic_workflow(tmp_path, lambda message: None)
    assert calls == []


def test_console_auto_action_exposes_task_logs(monkeypatch, tmp_path):
    project = tmp_path / "project"
    store = ProjectStore.create(
        project_dir=project,
        game_path=tmp_path,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
    )
    store.close()

    selected = []

    def fake_workflow(project_dir, log, provider_id=None):
        selected.append(provider_id)
        log("Step 1/3: Scan game text")
        log("Step 2/3: Translate")
        log("Step 3/3: QA")
        return {"qa": {"entries_with_errors": 0}}

    monkeypatch.setattr("agl.api.project_console.run_automatic_workflow", fake_workflow)
    with TestClient(create_console_app(project)) as client:
        started = client.post("/api/actions/auto", json={"provider": "chosen"})
        assert started.status_code == 200
        task_id = started.json()["task_id"]
        for _ in range(30):
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["status"] != "running":
                break
            time.sleep(0.02)

    assert task["status"] == "completed"
    assert selected == ["chosen"]
    assert any("Step 3/3: QA" in line for line in task["logs"])


def test_auto_workflow_uses_selected_provider(monkeypatch, tmp_path):
    selected = []
    provider = SimpleNamespace(is_ready=True, is_local_endpoint=False)
    monkeypatch.setattr(
        automatic_workflow,
        "load_config",
        lambda: SimpleNamespace(first_available_provider=lambda: "other", get_provider=lambda name: provider if name == "chosen" else None),
    )
    monkeypatch.setattr(automatic_workflow, "scan_project", lambda **kwargs: {"entries": 1, "errors": 0})
    monkeypatch.setattr(automatic_workflow, "translate_project", lambda **kwargs: selected.append(kwargs["provider_id"]) or {"failed": 0})
    monkeypatch.setattr(automatic_workflow, "run_qa_project", lambda **kwargs: {})

    automatic_workflow.run_automatic_workflow(tmp_path, lambda _: None, provider_id="chosen")
    assert selected == ["chosen"]

    with pytest.raises(RuntimeError, match="找不到 Provider"):
        automatic_workflow.run_automatic_workflow(tmp_path, lambda _: None, provider_id="unknown")
