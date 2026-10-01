from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agl import workspace_projects
from agl.api.desktop import create_desktop_app
from agl.api.project_console import create_app as create_console_app
from agl.api.workspace import create_workspace_app


def _build_rpgmaker_game(root: Path) -> Path:
    game = root / "DemoGame"
    data = game / "data"
    data.mkdir(parents=True)
    (data / "Items.json").write_text(
        json.dumps([None, {"id": 1, "name": "Potion", "description": "Restores HP"}]),
        encoding="utf-8",
    )
    return game


def test_single_server_desktop_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("AGL_PATCHES_ROOT", str(tmp_path / "patches"))
    app = create_desktop_app()

    with TestClient(app) as client:
        portal = client.get("/")
        assert portal.status_code == 200
        assert "providerReadiness" in portal.text
        health = client.get("/api/health")
        assert health.json() == {
            "status": "ok",
            "version": "0.20.0",
            "runtime": "desktop-single-server",
        }
        readiness = client.get("/api/readiness")
        assert readiness.status_code == 200
        assert set(readiness.json()) == {
            "provider_ready",
            "provider",
            "provider_mode",
            "supported_engines",
        }
        selected = client.post("/api/choose_mode", json={"mode": "simple"})
        assert selected.status_code == 200
        assert selected.json()["url"] == "/player/"

        player = client.get("/player/")
        assert player.status_code == 200
        assert "一键扫描 → 翻译 → QA" in player.text
        assert "playerTargetLang" in player.text
        assert 'id="folderError"' in player.text
        assert "gamePath=p.path" in player.text
        assert "/player/api/" not in player.text  # Prefixing happens at fetch runtime.
        preferences = client.get("/player/api/preferences")
        assert preferences.status_code == 200
        assert preferences.json()["target_language"]

        workspace = client.get("/workspace/")
        settings = client.get("/settings/")
        assert workspace.status_code == 200
        assert settings.status_code == 200
        assert 'href="/workspace/"' in workspace.text
        assert 'href="/settings/"' in workspace.text
        assert 'href="/?open=advanced"' not in workspace.text
        assert '<div class="side-label">Editor</div>' not in workspace.text
        assert '<div class="side-label">Quality</div>' not in workspace.text
        assert '<div class="side-label">Release</div>' not in workspace.text
        assert '<div class="side-label">Editor</div>' not in settings.text
        storage = client.get("/workspace/api/storage")
        assert storage.status_code == 200
        assert storage.json()["projects_root"] == str(tmp_path / "projects")

        untrusted = client.get("/api/health", headers={"host": "malicious.example"})
        assert untrusted.status_code == 400

        cross_site = client.post(
            "/api/reset_mode",
            headers={"origin": "https://malicious.example"},
        )
        assert cross_site.status_code == 403


def test_workspace_mounts_project_console_without_child_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("AGL_PATCHES_ROOT", str(tmp_path / "patches"))
    game = _build_rpgmaker_game(tmp_path)
    app = create_desktop_app()

    with TestClient(app) as client:
        created = client.post(
            "/workspace/api/projects/create",
            json={"name": "Demo", "game_path": str(game), "target_language": "zh-CN"},
        )
        assert created.status_code == 200, created.text

        opened = client.post("/workspace/api/projects/open", json={"name": "Demo"})
        assert opened.status_code == 200, opened.text
        console_url = opened.json()["url"]
        assert console_url.startswith("/console/")
        console = client.get(console_url)
        assert console.status_code == 200
        assert '<div class="side-label">Editor</div>' in console.text
        assert '<div class="side-label">Quality</div>' in console.text
        assert '<div class="side-label">Release</div>' in console.text
        assert 'id="consoleProvider"' in console.text
        assert 'href="/workspace/"' in console.text
        assert 'href="/settings/"' in console.text
        provider_info = client.get(console_url.rstrip("/") + "/api/config/providers")
        assert provider_info.status_code == 200
        assert "default" in provider_info.json()


def test_workspace_uses_native_game_folder_picker(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(
        "agl.api.player.select_folder_native",
        lambda: r"D:\Games\Demo Game",
    )
    app = create_desktop_app()

    with TestClient(app) as client:
        response = client.post("/workspace/api/select_folder")

    assert response.status_code == 200
    assert response.json() == {"path": r"D:\Games\Demo Game"}


def test_player_picker_and_preflight_resolve_selected_parent(monkeypatch, tmp_path):
    game = _build_rpgmaker_game(tmp_path / "日本語")
    monkeypatch.setattr("agl.api.player.choose_game_folder", lambda: str(game.parent))
    app = create_desktop_app()

    with TestClient(app) as client:
        picked = client.post("/player/api/select_folder")
        preflight = client.post(
            "/player/api/preflight",
            json={"game_path": picked.json()["path"], "target_language": "zh-CN"},
        )

    assert picked.json() == {"path": str(game.parent)}
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["path"] == str(game)
    assert preflight.json()["auto_resolved"] is True


def test_workspace_can_restore_archived_project(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    game = _build_rpgmaker_game(tmp_path)
    app = create_desktop_app()

    with TestClient(app) as client:
        assert client.post(
            "/workspace/api/projects/create",
            json={"name": "Demo", "game_path": str(game)},
        ).status_code == 200
        archived = client.post(
            "/workspace/api/projects/archive", json={"name": "Demo"}
        )
        archive_id = archived.json()["archive_id"]
        listed = client.get("/workspace/api/projects/archived")
        assert listed.json()["items"][0]["archive_id"] == archive_id
        restored = client.post(
            "/workspace/api/projects/restore",
            json={"archive_id": archive_id},
        )

    assert restored.status_code == 200
    assert restored.json()["name"] == "Demo"


def test_workspace_rejects_oversized_project_zip_upload(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(workspace_projects, "MAX_PROJECT_ZIP_UPLOAD_BYTES", 4)
    app = create_desktop_app()

    with TestClient(app) as client:
        response = client.post(
            "/workspace/api/projects/import",
            files={"file": ("project.zip", b"12345", "application/zip")},
        )

    assert response.status_code == 400
    assert "exceeds" in response.json()["detail"]


def test_console_rejects_patch_paths_instead_of_rewriting_them(tmp_path):
    project = tmp_path / "Project"
    project.mkdir()
    (project / "project.json").write_text("{}", encoding="utf-8")
    (project / "patches" / "foobar").mkdir(parents=True)

    with TestClient(create_console_app(project)) as client:
        response = client.post(
            "/api/actions/install",
            json={"patch_path": "foo/bar", "backup": True},
        )

    assert response.status_code == 422


def test_standalone_workspace_mounts_console_on_same_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    game = _build_rpgmaker_game(tmp_path)

    with TestClient(create_workspace_app()) as client:
        created = client.post(
            "/api/projects/create",
            json={"name": "Demo", "game_path": str(game)},
        )
        assert created.status_code == 200
        opened = client.post("/api/projects/open", json={"name": "Demo"})
        assert opened.status_code == 200
        data = opened.json()
        assert "port" not in data
        assert data["url"].startswith("/console/")
        assert client.get(data["url"]).status_code == 200
