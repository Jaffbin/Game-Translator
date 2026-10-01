from __future__ import annotations

import os
from pathlib import Path

from agl import workspace
from agl.legacy_migration import migrate_legacy_packaged_data


def _old_build(release: Path, name: str) -> Path:
    root = release / name / "AutoGameLocalizer"
    root.mkdir(parents=True)
    (root / "AutoGameLocalizer.exe").write_bytes(b"exe")
    return root


def _project(root: Path, name: str, data: bytes, stamp: int) -> Path:
    project = root / "projects" / name
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"' + name + '"}', encoding="utf-8")
    database = project / "entries.db"
    database.write_bytes(data)
    os.utime(database, ns=(stamp, stamp))
    return project


def test_packaged_projects_and_settings_survive_replacing_exe(monkeypatch, tmp_path):
    release = tmp_path / "release"
    old = _old_build(release, "old-build")
    newer = _old_build(release, "new-build")
    old_project = _project(old, "Same_Game", b"older", 1_000_000_000)
    _project(newer, "Same_Game", b"newer", 2_000_000_000)
    _project(old, "Other_Game", b"other", 1_000_000_000)
    _project(old, "_archived/Archived_Game", b"archive", 1_000_000_000)
    (newer / "config.toml").write_text("[translation]\ntarget_language='ja'\n", encoding="utf-8")
    (newer / "user_settings.json").write_text('{"first_run_completed":true}', encoding="utf-8")
    monkeypatch.setattr(workspace.sys, "frozen", True, raising=False)
    monkeypatch.setattr(workspace.sys, "platform", "win32")
    monkeypatch.setattr(workspace.sys, "executable", str(newer / "AutoGameLocalizer.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-data"))
    monkeypatch.delenv("AGL_PROJECTS_ROOT", raising=False)

    stable = tmp_path / "local-data" / "AutoGameLocalizer"
    assert workspace.app_root() == stable
    report = migrate_legacy_packaged_data()
    assert report["errors"] == []
    assert report["projects_imported"] == 3
    assert (stable / "projects" / "Same_Game" / "entries.db").read_bytes() == b"newer"
    assert (stable / "projects" / "Other_Game" / "entries.db").read_bytes() == b"other"
    assert (stable / "projects" / "_archived" / "Archived_Game" / "entries.db").read_bytes() == b"archive"
    assert (stable / "config.toml").is_file()
    assert (stable / "user_settings.json").is_file()
    assert old_project.is_dir()  # Migration must not move or delete old projects.

    (stable / "projects" / "Same_Game" / "entries.db").write_bytes(b"user changes")
    assert migrate_legacy_packaged_data()["projects_imported"] == 0
    assert (stable / "projects" / "Same_Game" / "entries.db").read_bytes() == b"user changes"
