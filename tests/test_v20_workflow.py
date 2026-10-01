from __future__ import annotations

import json
import sqlite3
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from agl.config import AppConfig, ProviderConfig
from agl.config import load_config
from agl.models import TranslationEntry
from agl.providers import OpenAICompatibleProvider, TranslationProviderError
from agl.pipeline import translate_one
from agl.cache import TranslationMemory
from agl.project import PROJECT_SCHEMA_VERSION, ProjectStore
from agl.services.player_workflow import PlayerWorkflow
from agl.services import translation
from agl.services import delivery, quality, scan
from agl import workspace_projects
from agl import config_manager
from agl import workspace
from agl.engines import detect_handler, resolve_game_folder


def _game(root: Path) -> Path:
    game = root / "Game"
    data = game / "data"
    data.mkdir(parents=True)
    (data / "Items.json").write_text(
        json.dumps([None, {"id": 1, "name": "Potion", "description": "Restores HP"}]),
        encoding="utf-8",
    )
    return game


def test_engine_detection_does_not_treat_any_data_folder_as_rpgmaker(tmp_path):
    game = tmp_path / "UnityGame"
    data = game / "data"
    data.mkdir(parents=True)
    (data / "unrelated.json").write_text("{}", encoding="utf-8")
    (game / "UnityPlayer.dll").write_bytes(b"")

    handler = detect_handler(str(game))

    assert handler is not None
    assert handler.engine_id == "unity_lightweight"


def test_packaged_windows_secret_fallback_survives_exe_directory_change(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace.sys, "frozen", True, raising=False)
    monkeypatch.setattr(workspace.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))

    path = workspace.secrets_env_path()

    assert path == tmp_path / "user-data" / "AutoGameLocalizer" / ".env"
    assert path.parent.is_dir()


def test_game_folder_resolution_accepts_unicode_parent_and_data_folder(tmp_path):
    game = _game(tmp_path / "ゲーム集")
    parent = game.parent

    resolved, handler = resolve_game_folder(parent)
    assert resolved == game
    assert handler.engine_id == "rpgmaker_mv_mz"
    assert resolve_game_folder(game / "data")[0] == game

    preflight = PlayerWorkflow().preflight(parent)
    assert preflight["path"] == str(game)
    assert preflight["auto_resolved"] is True


def test_game_folder_resolution_does_not_guess_between_games(tmp_path):
    _game(tmp_path / "First")
    _game(tmp_path / "Second")

    with pytest.raises(ValueError, match="多个游戏"):
        resolve_game_folder(tmp_path)


def test_concurrent_project_creation_reserves_name_once(monkeypatch, tmp_path):
    game = _game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))

    def create():
        try:
            return workspace_projects.create_project("Demo", str(game))["name"]
        except FileExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))

    assert sorted(results) == ["Demo", "exists"]
    assert (tmp_path / "projects" / "Demo" / "project.json").is_file()


def test_archived_project_can_be_listed_and_restored(monkeypatch, tmp_path):
    game = _game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    workspace_projects.create_project("Demo", str(game))

    archived = workspace_projects.archive_project("Demo")
    items = workspace_projects.list_archived_projects()

    assert [item["archive_id"] for item in items] == [archived["archive_id"]]
    restored = workspace_projects.restore_archived_project(archived["archive_id"])
    assert restored["name"] == "Demo"
    assert (tmp_path / "projects" / "Demo" / "project.json").is_file()
    assert workspace_projects.list_archived_projects() == []


def test_archived_project_restore_never_overwrites_active_project(monkeypatch, tmp_path):
    game = _game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    workspace_projects.create_project("Demo", str(game))
    archived = workspace_projects.archive_project("Demo")
    workspace_projects.create_project("Demo", str(game))

    with pytest.raises(FileExistsError):
        workspace_projects.restore_archived_project(archived["archive_id"])

    restored = workspace_projects.restore_archived_project(
        archived["archive_id"], target_name="Demo Restored"
    )
    assert restored["name"] == "Demo_Restored"


def test_scan_can_cancel_without_reconciling_partial_results(monkeypatch, tmp_path):
    game = _game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    created = workspace_projects.create_project("Demo", str(game))

    result = scan.scan_project(created["path"], should_cancel=lambda: True)

    assert result["cancelled"] is True
    assert result["processed_files"] == 0
    assert result["auto_ignored"] == 0
    with ProjectStore(created["path"]) as store:
        assert store.all_entries() == []


def test_player_requires_real_provider_before_project_creation(monkeypatch, tmp_path):
    game = _game(tmp_path)
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(
        "agl.services.player_workflow.load_config",
        lambda: AppConfig(
            providers={"mock": ProviderConfig(id="mock", type="mock")}
        ),
    )
    workflow = PlayerWorkflow()
    check = workflow.preflight(game)
    assert check["engine"] == "rpgmaker_mv_mz"
    assert check["provider_ready"] is False

    workflow.prepare(game, "zh-CN")
    deadline = time.monotonic() + 2
    while workflow.snapshot()["busy"] and time.monotonic() < deadline:
        time.sleep(0.01)
    state = workflow.snapshot()
    assert state["state"] == "configuration_required"
    assert not (tmp_path / "projects").exists()


def test_legacy_database_gets_version_and_recovery_copy(tmp_path):
    project_dir = tmp_path / "project"
    store = ProjectStore.create(
        project_dir=project_dir,
        game_path=tmp_path,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
    )
    store.close()

    with sqlite3.connect(project_dir / "entries.db") as conn:
        conn.execute("DROP TABLE schema_meta")
        conn.commit()

    with ProjectStore(project_dir) as migrated:
        version = migrated.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert int(version) == PROJECT_SCHEMA_VERSION
    assert (project_dir / "entries.pre_v2.db").is_file()


def test_translation_reports_per_entry_progress(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    store = ProjectStore.create(
        project_dir=project_dir,
        game_path=tmp_path,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
    )
    store.upsert_entries(
        [
            TranslationEntry(
                id="entry-1",
                source_text="Hello",
                file_path="data/Map001.json",
                engine="rpgmaker_mv_mz",
                location={"path": ["events", 1]},
            )
        ]
    )
    store.close()
    config = AppConfig(
        target_language="zh-CN",
        cache_db=tmp_path / "memory.db",
        providers={"mock": ProviderConfig(id="mock", type="mock")},
    )
    monkeypatch.setattr(translation, "load_config", lambda: config)
    updates = []
    result = translation.translate_project(
        project_dir,
        provider_id="mock",
        progress=updates.append,
    )
    assert result["translated"] == 1
    assert [item["processed"] for item in updates] == [0, 1]
    assert updates[-1]["total"] == 1


def test_auto_translation_stops_after_provider_failure(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    store = ProjectStore.create(
        project_dir=project_dir,
        game_path=tmp_path,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
    )
    store.upsert_entries([
        TranslationEntry(id=f"entry-{index}", source_text=f"Line {index}",
                         file_path="data/Items.json", engine="rpgmaker_mv_mz",
                         location={"path": [index, "name"]})
        for index in range(3)
    ])
    store.close()
    config = AppConfig(
        target_language="zh-CN",
        cache_db=tmp_path / "memory.db",
        providers={"cloud": ProviderConfig(id="cloud", type="openai_compatible",
                                            base_url="https://api.example.test/chat/completions",
                                            model="example", api_key_env="CLOUD_API_KEY")},
    )
    monkeypatch.setattr(translation, "load_config", lambda: config)
    calls = []

    class FailingProvider:
        id = "cloud"
        model = "example"

        def translate(self, **kwargs):
            calls.append(kwargs)
            raise TranslationProviderError("HTTP 401: invalid key")

    monkeypatch.setattr(translation, "create_provider", lambda *args, **kwargs: FailingProvider())
    result = translation.translate_project(project_dir, provider_id="cloud", stop_on_provider_error=True)

    assert len(calls) == 1
    assert result["failed"] == 1
    assert "401" in result["fatal_error"]


def test_bulk_translation_prefers_human_reviewed_memory(tmp_path):
    memory = TranslationMemory(tmp_path / "memory.db")
    memory.put(
        source_text="Hello",
        target_language="zh-CN",
        translated_text="人工译文",
        provider="human",
        model="reviewed",
        human_reviewed=True,
    )

    class Provider:
        id = "cloud"
        model = "example"

        def translate(self, **kwargs):
            raise AssertionError("Provider should not be called for reviewed TM hits")

    result = translate_one(
        provider=Provider(),
        cache=memory,
        source_text="Hello",
        target_language="zh-CN",
        cache_namespace="cloud:example",
    )
    memory.close()

    assert result == "人工译文"


def test_player_blocks_partial_patch_when_translation_or_qa_fails(monkeypatch, tmp_path):
    workflow = PlayerWorkflow()
    workflow._set(state="prepared", project_dir=str(tmp_path), provider="cloud")
    monkeypatch.setattr(
        translation,
        "translate_project",
        lambda *args, **kwargs: {
            "translated": 4,
            "skipped": 0,
            "failed": 1,
            "cancelled": False,
        },
    )
    monkeypatch.setattr(
        quality,
        "run_qa_project",
        lambda *args, **kwargs: {"entries_with_errors": 1},
    )
    patch_called = []
    monkeypatch.setattr(delivery, "patch_project", lambda *a, **k: patch_called.append(True))
    workflow._translate_job(tmp_path, "cloud")
    state = workflow.snapshot()
    assert state["state"] == "needs_attention"
    assert not patch_called


def test_patch_name_rejects_path_traversal_before_writing(tmp_path):
    for unsafe in ("../outside", "nested/patch", r"nested\patch"):
        try:
            delivery.patch_project(tmp_path, patch_name=unsafe)
        except ValueError as exc:
            assert "patch_name" in str(exc)
        else:
            raise AssertionError(f"Unsafe patch name was accepted: {unsafe}")

    assert not (tmp_path.parent / "outside").exists()


def test_player_blocks_patch_on_qa_warnings(monkeypatch, tmp_path):
    workflow = PlayerWorkflow()
    workflow._set(state="prepared", project_dir=str(tmp_path), provider="cloud")
    monkeypatch.setattr(
        translation,
        "translate_project",
        lambda *args, **kwargs: {
            "translated": 1,
            "skipped": 0,
            "failed": 0,
            "cancelled": False,
        },
    )
    monkeypatch.setattr(
        quality,
        "run_qa_project",
        lambda *args, **kwargs: {
            "entries_with_errors": 0,
            "entries_with_warnings": 1,
        },
    )
    patch_called = []
    monkeypatch.setattr(delivery, "patch_project", lambda *a, **k: patch_called.append(True))

    workflow._translate_job(tmp_path, "cloud")

    assert workflow.snapshot()["state"] == "needs_attention"
    assert not patch_called


def test_player_stops_when_scan_has_file_errors(monkeypatch, tmp_path):
    workflow = PlayerWorkflow()
    monkeypatch.setattr(
        workflow,
        "preflight",
        lambda path, target_language="zh-CN": {
            "engine": "rpgmaker_mv_mz",
            "provider": "cloud",
            "provider_ready": True,
        },
    )
    monkeypatch.setattr(
        workflow,
        "_find_or_create_project",
        lambda game_path, target_language: ("Demo", tmp_path),
    )
    monkeypatch.setattr(delivery, "list_backups_project", lambda project: [])
    monkeypatch.setattr(
        scan,
        "scan_project",
        lambda *args, **kwargs: {"entries": 4, "errors": 1},
    )

    workflow._prepare_job(tmp_path, "zh-CN")

    state = workflow.snapshot()
    assert state["state"] == "error"
    assert state["step"] == "扫描未完成"


def test_player_auto_continues_from_scan_to_translation(monkeypatch, tmp_path):
    workflow = PlayerWorkflow()
    project_dir = tmp_path / "project"
    store = ProjectStore.create(
        project_dir=project_dir,
        game_path=tmp_path,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
    )
    store.upsert_entries([
        TranslationEntry(
            id="entry-1",
            source_text="Hello",
            file_path="data/Items.json",
            engine="rpgmaker_mv_mz",
            location={"path": [1, "name"]},
        )
    ])
    store.close()
    monkeypatch.setattr(
        workflow, "preflight",
        lambda path, target_language="zh-CN": {
            "engine": "rpgmaker_mv_mz",
            "provider": "gemini",
            "provider_ready": True,
        },
    )
    monkeypatch.setattr(workflow, "_find_or_create_project", lambda *args: ("Demo", project_dir))
    monkeypatch.setattr(delivery, "list_backups_project", lambda project: [])
    monkeypatch.setattr(scan, "scan_project", lambda *args, **kwargs: {"entries": 1, "errors": 0})
    translated = []
    monkeypatch.setattr(workflow, "_translate_job", lambda path, provider: translated.append((path, provider)))

    workflow._prepare_job(tmp_path, "zh-CN", auto_translate=True)

    assert translated == [(project_dir, "gemini")]
    assert workflow.snapshot()["state"] == "translating"


def test_player_rerun_qa_refreshes_patch_only_when_clean(monkeypatch, tmp_path):
    workflow = PlayerWorkflow()
    workflow._set(state="done", project_dir=str(tmp_path), patch="old-patch")
    monkeypatch.setattr(quality, "run_qa_project", lambda *args, **kwargs: {
        "entries_with_errors": 0, "entries_with_warnings": 0,
    })
    patch_calls = []
    monkeypatch.setattr(delivery, "patch_project", lambda *args, **kwargs: (
        patch_calls.append(True) or {"patch_dir": "new-patch"}
    ))

    workflow._rerun_qa_job(tmp_path)

    assert patch_calls == [True]
    assert workflow.snapshot()["state"] == "done"
    assert workflow.snapshot()["patch"] == "new-patch"


def test_player_rerun_qa_blocks_stale_patch_on_issue(monkeypatch, tmp_path):
    workflow = PlayerWorkflow()
    workflow._set(state="done", project_dir=str(tmp_path), patch="old-patch")
    monkeypatch.setattr(quality, "run_qa_project", lambda *args, **kwargs: {
        "entries_with_errors": 1, "entries_with_warnings": 0,
    })

    workflow._rerun_qa_job(tmp_path)

    assert workflow.snapshot()["state"] == "needs_attention"
    assert workflow.snapshot()["patch"] is None


def test_player_discovers_and_restores_existing_backup(monkeypatch, tmp_path):
    game = _game(tmp_path)
    project_dir = tmp_path / "projects" / "Demo_zh-CN"
    project_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "agl.services.player_workflow.workspace_projects.list_projects",
        lambda: [
            {
                "name": "Demo_zh-CN",
                "path": str(project_dir),
                "game_path": str(game),
                "target_language": "zh-CN",
            }
        ],
    )
    monkeypatch.setattr(
        delivery,
        "list_backups_project",
        lambda project: [{"id": "backup-1"}] if Path(project) == project_dir else [],
    )
    restored = []
    monkeypatch.setattr(
        delivery,
        "rollback_project",
        lambda project, **kwargs: restored.append(Path(project)) or {"restored": 2},
    )

    workflow = PlayerWorkflow()
    assert workflow.recovery(game, "zh-CN") == {
        "project": "Demo_zh-CN",
        "backup_count": 1,
    }
    assert workflow.snapshot()["backup_count"] == 1

    workflow.restore()
    deadline = time.monotonic() + 2
    while workflow.snapshot()["busy"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert workflow.snapshot()["state"] == "restored"
    assert restored == [project_dir]


def test_rescan_ignores_removed_sources_and_restores_reappearing_sources(tmp_path):
    game = _game(tmp_path)
    project_dir = tmp_path / "project"
    with ProjectStore.create(
        project_dir=project_dir,
        game_path=game,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
    ):
        pass

    first = scan.scan_project(project_dir)
    assert first["entries"] == 2
    with ProjectStore(project_dir) as store:
        entry = store.all_entries()[0]
        store.update_entry(
            entry.id,
            target_text="已翻译",
            status="machine_translated",
            machine_translated=True,
        )
        entry_id = entry.id

    (game / "data" / "Items.json").write_text("[]", encoding="utf-8")
    removed = scan.scan_project(project_dir)
    assert removed["entries"] == 0
    assert removed["auto_ignored"] == 2
    with ProjectStore(project_dir) as store:
        stale = store.get_entry(entry_id)
        assert stale is not None
        assert stale.ignored is True
        assert stale.status.value == "ignored"

    (game / "data" / "Items.json").write_text(
        json.dumps([None, {"id": 1, "name": "Potion", "description": "Restores HP"}]),
        encoding="utf-8",
    )
    reappeared = scan.scan_project(project_dir)
    assert reappeared["restored"] == 2
    with ProjectStore(project_dir) as store:
        restored_entry = store.get_entry(entry_id)
        assert restored_entry is not None
        assert restored_entry.ignored is False
        assert restored_entry.status.value == "needs_review"


def test_saved_provider_configuration_does_not_resurrect_deleted_presets(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[translation]\n"
        'target_language = "zh-CN"\n\n'
        "[providers.mock]\n"
        'type = "mock"\n\n'
        "[providers.custom]\n"
        'type = "openai_compatible"\n'
        'base_url = "https://example.invalid/v1/chat/completions"\n'
        'model = "example"\n'
        'api_key_env = "CUSTOM_API_KEY"\n',
        encoding="utf-8",
    )

    config = load_config(config_path=config_file, env_path=tmp_path / "missing.env")

    assert set(config.providers) == {"mock", "custom"}
    assert "nvidia" not in config.providers


def test_incomplete_cloud_provider_is_not_reported_ready(monkeypatch):
    monkeypatch.setenv("INCOMPLETE_API_KEY", "secret")
    config = AppConfig(
        providers={
            "mock": ProviderConfig(id="mock", type="mock"),
            "incomplete": ProviderConfig(
                id="incomplete",
                type="openai_compatible",
                base_url="https://api.example.test/v1/chat/completions",
                model="",
                api_key_env="INCOMPLETE_API_KEY",
            ),
        }
    )

    assert config.first_available_provider() == "mock"


def test_provider_reads_key_from_credential_store_after_restart(monkeypatch):
    monkeypatch.delenv("VAULT_API_KEY", raising=False)
    monkeypatch.setattr(
        "agl.config.get_secret",
        lambda name: "vault-secret" if name == "VAULT_API_KEY" else None,
    )
    provider = ProviderConfig(
        id="cloud",
        base_url="https://api.example.test/v1/chat/completions",
        model="example",
        api_key_env="VAULT_API_KEY",
    )

    assert provider.api_key == "vault-secret"
    assert provider.is_ready is True


def test_settings_provider_test_loads_local_secret_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("AGL_TEST_API_KEY", raising=False)
    fallback = tmp_path / ".env"
    fallback.write_text("AGL_TEST_API_KEY=test-only\n", encoding="utf-8")
    monkeypatch.setattr(config_manager, "env_path", lambda: fallback)
    monkeypatch.setattr(
        config_manager,
        "read_config_data",
        lambda: {
            "translation": {"target_language": "zh-CN"},
            "providers": {"sample": {
                "type": "openai_compatible",
                "base_url": "https://example.test/chat/completions",
                "model": "sample",
                "api_key_env": "AGL_TEST_API_KEY",
            }},
        },
    )

    class FakeProvider:
        model = "sample"

        def translate(self, **kwargs):
            assert kwargs["text"] == "Hello."
            assert __import__("os").environ["AGL_TEST_API_KEY"] == "test-only"
            return "你好。"

    monkeypatch.setattr(config_manager, "create_provider", lambda config: FakeProvider())
    try:
        result = config_manager.test_provider("sample")
        assert result["translated_text"] == "你好。"
    finally:
        __import__("os").environ.pop("AGL_TEST_API_KEY", None)


def test_cloud_is_preferred_over_keyless_local_provider(monkeypatch):
    monkeypatch.setenv("CLOUD_API_KEY", "secret")
    config = AppConfig(
        providers={
            "ollama": ProviderConfig(
                id="ollama",
                base_url="http://127.0.0.1:11434/v1/chat/completions",
                model="qwen",
            ),
            "cloud": ProviderConfig(
                id="cloud",
                base_url="https://api.example.test/v1/chat/completions",
                model="example",
                api_key_env="CLOUD_API_KEY",
            ),
        }
    )

    assert config.providers["ollama"].is_ready is True
    assert OpenAICompatibleProvider(config.providers["ollama"])._headers() == {
        "Content-Type": "application/json"
    }
    assert config.first_available_provider() == "cloud"


def test_local_provider_requires_running_endpoint(monkeypatch):
    config = AppConfig(
        providers={
            "mock": ProviderConfig(id="mock", type="mock"),
            "ollama": ProviderConfig(
                id="ollama",
                base_url="http://127.0.0.1:11434/v1/chat/completions",
                model="qwen2.5:7b",
            ),
        }
    )
    monkeypatch.setattr(
        ProviderConfig,
        "local_endpoint_reachable",
        lambda self, timeout_seconds=0.2: False,
    )
    assert config.first_available_provider() == "mock"

    monkeypatch.setattr(
        ProviderConfig,
        "local_endpoint_reachable",
        lambda self, timeout_seconds=0.2: True,
    )
    assert config.first_available_provider() == "ollama"


def test_project_zip_import_is_atomic_and_updates_project_name(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(projects))
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "Original/project.json",
            json.dumps({"name": "Original", "game_path": "C:/Games/Demo"}),
        )
        zf.writestr("Original/entries.db", b"database-placeholder")

    imported = workspace_projects.import_project_zip(archive, target_name="Renamed")

    imported_dir = Path(imported["path"])
    assert imported_dir.name == "Renamed"
    meta = json.loads((imported_dir / "project.json").read_text(encoding="utf-8"))
    assert meta["name"] == "Renamed"
    assert not list(projects.glob(".import-*"))


def test_project_zip_import_cleans_staging_after_unsafe_path(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(projects))
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.json", json.dumps({"name": "Unsafe"}))
        zf.writestr("../outside.txt", "must not escape")

    try:
        workspace_projects.import_project_zip(archive)
    except ValueError as exc:
        assert "Unsafe ZIP path" in str(exc)
    else:
        raise AssertionError("Unsafe project ZIP was accepted")

    assert not (projects / "Unsafe").exists()
    assert not (tmp_path / "outside.txt").exists()
    assert not list(projects.glob(".import-*"))


def test_direct_project_zip_import_enforces_compressed_size_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(tmp_path / "projects"))
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.json", json.dumps({"name": "Demo"}))
    monkeypatch.setattr(workspace_projects, "MAX_PROJECT_ZIP_UPLOAD_BYTES", 4)

    with pytest.raises(ValueError, match="compressed-size limit"):
        workspace_projects.import_project_zip(archive)


def test_project_zip_export_is_consistent_and_excludes_old_archives(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setenv("AGL_PROJECTS_ROOT", str(projects))
    project_dir = projects / "Demo"
    store = ProjectStore.create(
        project_dir=project_dir,
        game_path=tmp_path,
        engine_id="rpgmaker_mv_mz",
        target_language="zh-CN",
        name="Demo",
    )
    store.upsert_entries(
        [
            TranslationEntry(
                id="entry-in-wal",
                source_text="Hello",
                file_path="data/Map001.json",
                engine="rpgmaker_mv_mz",
                location={"path": ["events", 1]},
            )
        ]
    )
    exports = project_dir / "exports"
    exports.mkdir()
    (exports / "previous.zip").write_bytes(b"old archive")
    (exports / "translations.csv").write_text("source,target\n", encoding="utf-8")

    archive = workspace_projects.export_project_zip("Demo")
    store.close()

    extracted_db = tmp_path / "exported.db"
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert "exports/previous.zip" not in names
        assert "exports/translations.csv" in names
        extracted_db.write_bytes(zf.read("entries.db"))

    with sqlite3.connect(extracted_db) as conn:
        assert conn.execute(
            "SELECT source_text FROM entries WHERE id = ?",
            ("entry-in-wal",),
        ).fetchone()[0] == "Hello"


def test_provider_settings_reject_invalid_url_and_retry_limits(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    base = {
        "id": "cloud",
        "type": "openai_compatible",
        "model": "example-model",
        "api_key_env": "CLOUD_API_KEY",
    }

    for invalid in (
        {**base, "base_url": "file:///tmp/endpoint"},
        {**base, "base_url": "https://api.example.test/v1", "max_retries": -1},
        {**base, "base_url": "https://api.example.test/v1", "timeout_seconds": 0},
    ):
        try:
            config_manager.upsert_provider(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid provider settings were accepted: {invalid}")


def test_provider_does_not_retry_permanent_http_errors(monkeypatch):
    config = ProviderConfig(
        id="cloud",
        base_url="https://api.example.test/v1/chat/completions",
        model="example",
        api_key_env="CLOUD_API_KEY",
        max_retries=3,
        retry_backoff_seconds=0,
    )
    monkeypatch.setenv("CLOUD_API_KEY", "secret")
    calls = []

    class Response:
        status_code = 401
        text = "unauthorized"

        def raise_for_status(self):
            import requests

            raise requests.HTTPError("401")

    monkeypatch.setattr(
        "agl.providers.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response(),
    )

    try:
        OpenAICompatibleProvider(config).translate("Hello", "zh-CN")
    except TranslationProviderError as exc:
        assert "HTTP 401" in str(exc)
    else:
        raise AssertionError("Permanent provider error was accepted")

    assert len(calls) == 1


def test_provider_stops_immediately_when_quota_is_exhausted(monkeypatch):
    config = ProviderConfig(
        id="gemini",
        base_url="https://api.example.test/v1/chat/completions",
        model="example",
        api_key_env="CLOUD_API_KEY",
        max_retries=3,
    )
    monkeypatch.setenv("CLOUD_API_KEY", "secret")
    calls = []

    class Response:
        status_code = 429
        text = '{"error":{"message":"You exceeded your current quota, please check your plan and billing details."}}'

    monkeypatch.setattr(
        "agl.providers.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response(),
    )

    with pytest.raises(TranslationProviderError, match="配额已用尽"):
        OpenAICompatibleProvider(config).translate("Hello", "zh-CN")
    assert len(calls) == 1


def test_provider_rejects_non_string_content(monkeypatch):
    config = ProviderConfig(
        id="cloud",
        base_url="https://api.example.test/v1/chat/completions",
        model="example",
        api_key_env="CLOUD_API_KEY",
        max_retries=0,
    )
    monkeypatch.setenv("CLOUD_API_KEY", "secret")

    class Response:
        status_code = 200
        text = "ok"

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": None}}]}

    monkeypatch.setattr("agl.providers.requests.post", lambda *a, **k: Response())

    try:
        OpenAICompatibleProvider(config).translate("Hello", "zh-CN")
    except TranslationProviderError as exc:
        assert "non-empty string" in str(exc)
    else:
        raise AssertionError("Invalid provider response was accepted")
