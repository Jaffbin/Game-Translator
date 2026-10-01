from __future__ import annotations

import datetime
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from .engines import detect_handler
from .io_utils import atomic_write_text
from .project import ProjectStore
from .workspace import (
    projects_root,
    sanitize_project_name,
)


MAX_PROJECT_ZIP_FILES = 10_000
MAX_PROJECT_ZIP_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_PROJECT_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_PROJECT_ZIP_MEMBER_BYTES = 128 * 1024 * 1024


def _now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


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

    try:
        # Reserve the name atomically so two UI requests cannot initialize the
        # same project directory at the same time.
        project_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise FileExistsError(
            f"Project folder already exists: {project_dir}"
        ) from exc

    try:
        store = ProjectStore.create(
            project_dir=project_dir,
            game_path=game_path_obj,
            engine_id=handler.engine_id,
            target_language=target_language.strip(),
            name=safe_name,
        )
        store.close()
    except Exception:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise

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
        "archive_id": target.name,
        "archived_path": str(target.resolve()),
    }


def list_archived_projects() -> List[Dict[str, Any]]:
    """List recoverable projects previously moved into ``_archived``."""
    archive_root = projects_root() / "_archived"
    if not archive_root.is_dir():
        return []

    items: List[Dict[str, Any]] = []
    resolved_root = archive_root.resolve()
    for project_dir in sorted(archive_root.iterdir(), reverse=True):
        if not project_dir.is_dir():
            continue
        try:
            resolved = project_dir.resolve()
            if not resolved.is_relative_to(resolved_root):
                continue
            meta = json.loads((resolved / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        items.append(
            {
                "archive_id": project_dir.name,
                "name": str(meta.get("name") or project_dir.name),
                "engine": str(meta.get("engine") or ""),
                "target_language": str(meta.get("target_language") or ""),
                "archived_path": str(resolved),
            }
        )
    return items


def restore_archived_project(
    archive_id: str,
    target_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Restore one archived project without overwriting an active project."""
    archive_id = (archive_id or "").strip()
    if not archive_id or Path(archive_id).name != archive_id or archive_id in {".", ".."}:
        raise ValueError("Invalid archive identifier.")

    root = projects_root()
    archive_root = (root / "_archived").resolve()
    archived = (archive_root / archive_id).resolve()
    if not archived.is_relative_to(archive_root) or not archived.is_dir():
        raise FileNotFoundError(f"Archived project not found: {archive_id}")

    project_file = archived / "project.json"
    if not project_file.is_file():
        raise FileNotFoundError("Archived project is missing project.json.")
    meta = json.loads(project_file.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError("Archived project metadata must contain an object.")

    final_name = sanitize_project_name(target_name or meta.get("name") or archive_id)
    target = root / final_name
    if target.exists():
        raise FileExistsError(f"Project already exists: {target}")

    archived.replace(target)
    try:
        if meta.get("name") != final_name:
            meta["name"] = final_name
            atomic_write_text(
                target / "project.json",
                json.dumps(meta, ensure_ascii=False, indent=2),
            )
    except Exception:
        if not archived.exists() and target.exists():
            target.replace(archived)
        raise

    return {"name": final_name, "path": str(target.resolve())}


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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        temp_zip = Path(tmp.name)
    temp_db: Optional[Path] = None

    include_files = [
        "project.json",
        "entries.db",
        "glossary.csv",
    ]

    include_dirs = [
        "exports",
        "reports",
    ]

    try:
        with zipfile.ZipFile(
            temp_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zf:
            for file_name in include_files:
                file_path = project_dir / file_name

                if not file_path.exists():
                    continue

                if file_name == "entries.db":
                    # WAL mode may hold committed rows outside entries.db. A
                    # SQLite backup captures a consistent, self-contained DB.
                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=".db",
                    ) as tmp:
                        temp_db = Path(tmp.name)
                    source = sqlite3.connect(str(file_path), timeout=30.0)
                    target = sqlite3.connect(str(temp_db))
                    try:
                        source.backup(target)
                    finally:
                        target.close()
                        source.close()
                    zf.write(temp_db, arcname=file_name)
                else:
                    zf.write(file_path, arcname=file_name)

            for dir_name in include_dirs:
                dir_path = project_dir / dir_name

                if not dir_path.exists():
                    continue

                for file_path in dir_path.rglob("*"):
                    # Project exports live in this directory too. Including old
                    # project ZIPs would make every new export recursively grow.
                    if file_path.is_file() and file_path.suffix.lower() != ".zip":
                        arcname = file_path.relative_to(project_dir).as_posix()
                        zf.write(file_path, arcname=arcname)

        exports_dir = project_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        final_zip = exports_dir / f"{safe_name}_{stamp}.zip"
        shutil.move(str(temp_zip), str(final_zip))
        return final_zip
    finally:
        if temp_db is not None:
            temp_db.unlink(missing_ok=True)
        temp_zip.unlink(missing_ok=True)


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
    if zip_path.stat().st_size > MAX_PROJECT_ZIP_UPLOAD_BYTES:
        raise ValueError("Project ZIP exceeds the 512 MB compressed-size limit.")

    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        if len(infos) > MAX_PROJECT_ZIP_FILES:
            raise ValueError("Project ZIP contains too many files.")
        total_size = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted project ZIP files are not supported.")
            if info.file_size > MAX_PROJECT_ZIP_MEMBER_BYTES:
                raise ValueError(f"Project ZIP member is too large: {info.filename}")
            total_size += info.file_size
            if total_size > MAX_PROJECT_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("Project ZIP expands beyond the 512 MB safety limit.")

        names = [info.filename for info in infos]

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

        workspace_root = projects_root()
        staging_dir = Path(tempfile.mkdtemp(prefix=".import-", dir=workspace_root))
        try:
            resolved_staging = staging_dir.resolve()

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

                dest = (staging_dir / rel).resolve()

                if not dest.is_relative_to(resolved_staging):
                    raise ValueError(
                        f"Unsafe ZIP path detected: {member}"
                    )

                if member.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    with zf.open(member) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)

            imported_project_file = staging_dir / "project.json"
            if not imported_project_file.is_file():
                raise ValueError("Invalid project ZIP: project.json was not extracted.")
            imported_meta = json.loads(imported_project_file.read_text(encoding="utf-8"))
            if not isinstance(imported_meta, dict):
                raise ValueError("Invalid project ZIP: project.json must contain an object.")
            imported_meta["name"] = final_name
            atomic_write_text(
                imported_project_file,
                json.dumps(imported_meta, ensure_ascii=False, indent=2),
            )

            if target_dir.exists():
                raise FileExistsError(f"Project already exists: {target_dir}")
            staging_dir.replace(target_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    return {
        "name": final_name,
        "path": str(target_dir.resolve()),
    }
