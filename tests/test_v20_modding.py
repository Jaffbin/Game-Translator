from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agl import workspace_projects
from agl.api.project_console import create_app
from agl.services import delivery
from agl.services import rpgmaker_modding as modding


def _build_game(root: Path) -> Path:
    game = root / "Game"
    data = game / "data"
    data.mkdir(parents=True)
    (data / "Items.json").write_text(
        json.dumps(
            [
                None,
                {
                    "id": 1,
                    "name": "Potion",
                    "description": "Restores HP",
                    "price": 50,
                    "consumable": True,
                    "speed": 0,
                    "successRate": 100,
                    "repeats": 1,
                    "tpGain": 0,
                },
            ]
        ),
        encoding="utf-8",
    )
    (data / "Enemies.json").write_text(
        json.dumps(
            [
                None,
                {
                    "id": 1,
                    "name": "Slime",
                    "exp": 10,
                    "gold": 5,
                    "params": [100, 10, 12, 8, 6, 6, 10, 8],
                },
            ]
        ),
        encoding="utf-8",
    )
    return game


def test_rpgmaker_mod_catalog_and_preview(tmp_path):
    game = _build_game(tmp_path)

    result = modding.catalog(game, "items", query="potion")
    preview = modding.preview(
        game,
        [{"category": "items", "record_id": 1, "values": {"price": 999}}],
    )

    assert result["total"] == 1
    assert result["items"][0]["values"]["price"] == 50
    assert preview["records_changed"] == 1
    assert preview["changes"][0]["fields"][0] == {
        "field": "price",
        "label": "Price",
        "before": 50,
        "after": 999,
    }


def test_rpgmaker_mod_rejects_unsafe_fields_and_values(tmp_path):
    game = _build_game(tmp_path)

    with pytest.raises(ValueError, match="not safely editable"):
        modding.preview(
            game,
            [{"category": "items", "record_id": 1, "values": {"note": "script"}}],
        )
    with pytest.raises(ValueError, match="at least 0"):
        modding.preview(
            game,
            [{"category": "items", "record_id": 1, "values": {"price": -1}}],
        )
    with pytest.raises(ValueError, match="must be an integer"):
        modding.preview(
            game,
            [{"category": "items", "record_id": 1, "values": {"price": True}}],
        )


def test_rpgmaker_mod_patch_install_and_rollback(monkeypatch, tmp_path):
    game = _build_game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    project = workspace_projects.create_project("Demo", str(game))
    items_file = game / "data" / "Items.json"
    original = items_file.read_bytes()

    patch = modding.build_patch(
        project["path"],
        [
            {"category": "items", "record_id": 1, "values": {"price": 999}},
            {"category": "enemies", "record_id": 1, "values": {"gold": 777, "param_0": 5000}},
        ],
        patch_name="test-data-mod",
    )
    installed = delivery.install_project(project["path"], patch["patch_dir"])
    modified = json.loads(items_file.read_text(encoding="utf-8"))

    assert patch["files"] == 2
    assert installed["installed"] == 2
    assert modified[1]["price"] == 999
    rolled_back = delivery.rollback_project(
        project["path"], backup_id=installed["backup_id"]
    )
    assert rolled_back["restored"] == 2
    assert items_file.read_bytes() == original


def test_rpgmaker_modding_console_api(monkeypatch, tmp_path):
    game = _build_game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    project = workspace_projects.create_project("Demo", str(game))
    app = create_app(Path(project["path"]))

    with TestClient(app) as client:
        categories = client.get("/api/modding/categories")
        catalog_response = client.get("/api/modding/catalog?category=items")
        preview_response = client.post(
            "/api/modding/preview",
            json={
                "changes": [
                    {"category": "items", "record_id": 1, "values": {"price": 123}}
                ]
            },
        )
        patch_response = client.post(
            "/api/modding/patch",
            json={
                "patch_name": "api-data-mod",
                "changes": [
                    {"category": "items", "record_id": 1, "values": {"price": 123}}
                ],
            },
        )

    assert categories.status_code == 200
    assert catalog_response.json()["items"][0]["name"] == "Potion"
    assert preview_response.json()["fields_changed"] == 1
    assert patch_response.status_code == 200
    assert Path(patch_response.json()["patch_dir"], "manifest.json").is_file()


def test_patch_list_uses_creation_time_not_patch_name(tmp_path):
    patches = tmp_path / "patches"
    older = patches / "z-old"
    newer = patches / "a-new"
    older.mkdir(parents=True)
    newer.mkdir()
    (older / "manifest.json").write_text("{}", encoding="utf-8")
    (newer / "manifest.json").write_text("{}", encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    assert [item["name"] for item in delivery.list_patches(tmp_path)] == [
        "a-new",
        "z-old",
    ]
