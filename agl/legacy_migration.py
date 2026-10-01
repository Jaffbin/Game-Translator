"""Copy packaged v20 data out of version-specific EXE directories once."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .workspace import app_root


_last_report: dict[str, Any] = {"projects_imported": 0, "errors": []}


def _legacy_roots(exe_dir: Path) -> list[Path]:
    roots = [exe_dir]
    # Prior release ZIPs use release/<build>/AutoGameLocalizer/AutoGameLocalizer.exe.
    # Do not search unrelated user directories for projects.
    if exe_dir.parent.parent.name.lower() != "release":
        return roots
    release_dir = exe_dir.parent.parent
    for build_dir in release_dir.iterdir():
        if not build_dir.is_dir() or build_dir.is_symlink():
            continue
        candidate = build_dir / "AutoGameLocalizer"
        if candidate.is_dir() and not candidate.is_symlink() and (candidate / "AutoGameLocalizer.exe").is_file():
            roots.append(candidate)
    return list(dict.fromkeys(roots))


def _newest_file(roots: list[Path], relative: Path) -> Path | None:
    files = [root / relative for root in roots if (root / relative).is_file()]
    return max(files, key=lambda path: path.stat().st_mtime_ns, default=None)


def _copy_file_if_missing(source: Path | None, target: Path) -> None:
    if source is None or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".migration-", dir=target.parent) as temp:
        staged = Path(temp) / target.name
        shutil.copy2(source, staged)
        if not target.exists():
            staged.rename(target)


def _project_stamp(project: Path) -> int:
    files = [project / "project.json", project / "entries.db"]
    return max((file.stat().st_mtime_ns for file in files if file.is_file()), default=0)


def _copy_projects(roots: list[Path], stable: Path, subfolder: str, report: dict[str, Any]) -> None:
    sources: dict[str, Path] = {}
    for root in roots:
        old_projects = root / "projects" / subfolder
        if not old_projects.is_dir():
            continue
        for project in old_projects.iterdir():
            if not project.is_dir() or project.is_symlink() or project.name == "_archived":
                continue
            if not (project / "project.json").is_file():
                continue
            previous = sources.get(project.name)
            if previous is None or _project_stamp(project) > _project_stamp(previous):
                sources[project.name] = project

    destination_root = stable / "projects" / subfolder
    destination_root.mkdir(parents=True, exist_ok=True)
    for name, source in sorted(sources.items()):
        destination = destination_root / name
        if destination.exists():
            continue
        try:
            with tempfile.TemporaryDirectory(prefix=".migration-", dir=destination_root) as temp:
                staged = Path(temp) / name
                shutil.copytree(source, staged, symlinks=True)
                if not destination.exists():
                    staged.rename(destination)
                    report["projects_imported"] += 1
        except OSError as exc:
            report["errors"].append(f"project {name}: {exc}")


def migrate_legacy_packaged_data() -> dict[str, Any]:
    """Preserve old build folders; import only when the stable store is new."""
    global _last_report
    report: dict[str, Any] = {"projects_imported": 0, "errors": []}
    _last_report = report
    if not getattr(sys, "frozen", False) or sys.platform != "win32" or os.getenv("AGL_PROJECTS_ROOT"):
        return report

    stable = app_root()
    marker = stable / ".legacy_migration_done"
    if marker.exists():
        return report

    exe_dir = Path(sys.executable).resolve().parent
    try:
        roots = _legacy_roots(exe_dir)
        if not roots:
            return report
        stable.mkdir(parents=True, exist_ok=True)
        for name in ("config.toml", "user_settings.json", ".env"):
            try:
                _copy_file_if_missing(_newest_file(roots, Path(name)), stable / name)
            except OSError as exc:
                report["errors"].append(f"{name}: {exc}")
        try:
            _copy_file_if_missing(
                _newest_file(roots, Path(".cache") / "translation.db"),
                stable / ".cache" / "translation.db",
            )
        except OSError as exc:
            report["errors"].append(f"translation memory: {exc}")

        _copy_projects(roots, stable, "", report)
        _copy_projects(roots, stable, "_archived", report)

        if not report["errors"]:
            marker.write_text("Copied from earlier EXE folders; originals were left in place.\n", encoding="utf-8")
    except OSError as exc:
        report["errors"].append(str(exc))
    return report


def last_migration_report() -> dict[str, Any]:
    return dict(_last_report)
