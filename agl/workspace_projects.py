from __future__ import annotations

import datetime
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from .engines import detect_handler
from .project import ProjectStore
from .workspace import (
    projects_root,
    sanitize_project_name,
)


def _now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _count_dirs(path: Path) -> int:
    if not path.exists():
        return 0

    return len(
        [
            item
            for item in path.iterdir()
            if item.is_dir()
        ]
    )


# ----------------------------------------------------------------------
# List
# ----------------------------------------------------------------------

def list_projects() -> List[Dict[str, Any]]:
    """
    List all valid projects under projects/.
    """
    root = projects_root()
    items: List[Dict[str, Any]] = []

    if not root.exists():
        return items

    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue

        if project_dir.name == "_archived":
            continue

        project_file = project_dir / "project.json"

        if not project_file.exists():
            continue

        try:
            meta = json.loads(project_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        items.append(
            {
                "name": project_dir.name,
                "path": str(project_dir.resolve()),
                "engine": meta.get("engine", ""),
                "target_language": meta.get("target_language", ""),
                "game_path": meta.get("game_path", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "patches": _count_dirs(project_dir / "patches"),
                "backups": _count_dirs(project_dir / "backups"),
            }
        )

    return items


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------

def create_project(
    name: str,
    game_path: str,
    target_language: str = "zh-CN",
) -> Dict[str, Any]:
    """
    Create a new project under projects/.
    """
    safe_name = sanitize_project_name(name)
    project_dir = projects_root() / safe_name

    if project_dir.exists():
        raise FileExistsError(
            f"Project folder already exists: {project_dir}"
        )

    game_path_obj = Path(game_path).expanduser().resolve()

    if not game_path_obj.exists():
        raise FileNotFoundError(
            f"Game path does not exist: {game_path_obj}"
        )

    handler = detect_handler(str(game_path_obj))

    if handler is None:
        raise RuntimeError(
            "Unsupported game or unknown engine. "
            "Supported engines: RPG Maker MV/MZ, Ren'Py templates, "
            "Unity lightweight text."
        )

    store = ProjectStore.create(
        project_dir=project_dir,
        game_path=game_path_obj,
        engine_id=handler.engine_id,
        target_language=target_language.strip(),
        name=safe_name,
    )

    store.close()

    return {
        "name": safe_name,
        "path": str(project_dir.resolve()),
        "engine": handler.engine_id,
        "target_language": target_language,
    }


# ----------------------------------------------------------------------
# Archive
# ----------------------------------------------------------------------

def archive_project(name: str) -> Dict[str, Any]:
    """
    Archive a project instead of permanently deleting it.
    """
    safe_name = sanitize_project_name(name)
    project_dir = projects_root() / safe_name

    if not project_dir.exists():
        raise FileNotFoundError(
            f"Project not found: {project_dir}"
        )

    if not (project_dir / "project.json").exists():
        raise FileNotFoundError(
            f"Invalid project, missing project.json: {project_dir}"
        )

    archive_root = projects_root() / "_archived"
    archive_root.mkdir(parents=True, exist_ok=True)

    stamp = _now_stamp()
    target = archive_root / f"{safe_name}_{stamp}"

    shutil.move(str(project_dir), str(target))

    return {
        "archived_path": str(target.resolve()),
    }


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------

def export_project_zip(name: str) -> Path:
    """
    Export project as ZIP.

    Includes:
      project.json
      entries.db
      glossary.csv if exists
      exports/
      reports/

    Excludes:
      backups/
      patches/
      .cache/
    """
    safe_name = sanitize_project_name(name)
    project_dir = projects_root() / safe_name

    if not project_dir.exists():
        raise FileNotFoundError(
            f"Project not found: {project_dir}"
        )

    if not (project_dir / "project.json").exists():
        raise FileNotFoundError(
            f"Invalid project, missing project.json: {project_dir}"
        )

    stamp = _now_stamp()
    temp_zip = Path(tempfile.gettempdir()) / f"{safe_name}_{stamp}.zip"

    include_files = [
        "project.json",
        "entries.db",
        "glossary.csv",
    ]

    include_dirs = [
        "exports",
        "reports",
    ]

    with zipfile.ZipFile(
        temp_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        for file_name in include_files:
            file_path = project_dir / file_name

            if file_path.exists():
                zf.write(file_path, arcname=file_name)

        for dir_name in include_dirs:
            dir_path = project_dir / dir_name

            if not dir_path.exists():
                continue

            for file_path in dir_path.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(project_dir).as_posix()
                    zf.write(file_path, arcname=arcname)

    exports_dir = project_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    final_zip = exports_dir / temp_zip.name

    shutil.move(str(temp_zip), str(final_zip))

    return final_zip


# ----------------------------------------------------------------------
# Import
# ----------------------------------------------------------------------

def import_project_zip(
    zip_path: Path | str,
    target_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Import a project ZIP into projects/.

    ZIP must contain project.json either at root:

      project.json
      entries.db

    or inside one top-level folder:

      MyGame_zh/project.json
      MyGame_zh/entries.db
    """
    zip_path = Path(zip_path)

    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP not found: {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

        project_json_name = None

        for name in names:
            pure = PurePosixPath(name)

            if pure.name == "project.json" and len(pure.parts) <= 2:
                project_json_name = name
                break

        if project_json_name is None:
            raise ValueError(
                "Invalid project ZIP: project.json not found."
            )

        meta = json.loads(
            zf.read(project_json_name).decode("utf-8")
        )

        pure_project_json = PurePosixPath(project_json_name)

        if len(pure_project_json.parts) > 1:
            prefix = pure_project_json.parts[0] + "/"
            default_name = pure_project_json.parts[0]
        else:
            prefix = ""
            default_name = meta.get("name", "ImportedProject")

        final_name = sanitize_project_name(
            target_name or meta.get("name") or default_name
        )

        target_dir = projects_root() / final_name

        if target_dir.exists():
            raise FileExistsError(
                f"Project already exists: {target_dir}"
            )

        target_dir.mkdir(parents=True)

        resolved_target = target_dir.resolve()

        for member in names:
            if prefix and not member.startswith(prefix):
                continue

            rel = member[len(prefix):]

            if not rel:
                continue

            if rel.startswith("__MACOSX"):
                continue

            rel_path = PurePosixPath(rel)

            if rel_path.name == ".DS_Store":
                continue

            dest = (target_dir / rel).resolve()

            if not dest.is_relative_to(resolved_target):
                raise ValueError(
                    f"Unsafe ZIP path detected: {member}"
                )

            if member.endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)

                with zf.open(member) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)

    return {
        "name": final_name,
        "path": str(target_dir.resolve()),
    }