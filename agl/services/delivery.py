from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..io_utils import atomic_write_text
from ..engines import get_handler_by_id
from ..installer import (
    install_patch,
    list_backups,
    rollback,
    sha256_file,
)
from ..models import EntryStatus
from ..project import ProjectStore
from ..version import __version__
from ..qa import (
    load_glossary,
    run_qa_entries,
)


LogFunc = Callable[[str], None]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _make_log(log: Optional[LogFunc]) -> LogFunc:
    if log is None:
        return lambda message: None
    return log


def list_patches(project_dir: Path | str) -> List[Dict[str, str]]:
    project_dir = Path(project_dir)
    patches_dir = project_dir / "patches"

    if not patches_dir.exists():
        return []

    result: List[Dict[str, str]] = []

    candidates = [item for item in patches_dir.iterdir() if item.is_dir()]
    candidates.sort(
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for patch_dir in candidates:
        if not (patch_dir / "manifest.json").exists():
            continue

        result.append(
            {
                "name": patch_dir.name,
                "path": str(patch_dir.resolve()),
            }
        )

    return result


# ----------------------------------------------------------------------
# Patch
# ----------------------------------------------------------------------

def patch_project(
    project_dir: Path | str,
    patch_name: Optional[str] = None,
    output: Optional[Path | str] = None,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    if patch_name is not None:
        patch_name = patch_name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", patch_name):
            raise ValueError(
                "patch_name must be one safe filename component (letters, numbers, '.', '_' or '-')."
            )

    with ProjectStore(project_dir) as store:
        handler = get_handler_by_id(store.meta["engine"])

        if handler is None:
            raise RuntimeError(
                f"No handler found for engine: {store.meta['engine']}"
            )

        game_root = Path(store.meta["game_path"])

        if not handler.detect(str(game_root)):
            raise RuntimeError(
                f"Game path is not valid for engine {handler.engine_id}: "
                f"{game_root}"
            )

        if output is None:
            if not patch_name:
                patch_name = datetime.datetime.now().strftime(
                    "%Y%m%d_%H%M%S_%f"
                )

            patch_dir = Path(project_dir) / "patches" / patch_name

            if patch_dir.exists():
                raise FileExistsError(
                    f"Patch directory already exists: {patch_dir}"
                )
        else:
            patch_dir = Path(output)

        patch_dir.mkdir(parents=True, exist_ok=True)

        entries = store.all_entries()

        # --------------------------------------------------------------
        # QA gate: block entries with QA errors.
        # --------------------------------------------------------------

        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary = load_glossary(glossary_path)

        qa_results, qa_stats = run_qa_entries(
            entries=entries,
            target_language=store.meta.get("target_language", ""),
            glossary=glossary,
        )

        blocked_entry_ids = {
            result.entry_id
            for result in qa_results
            if result.errors
        }

        if blocked_entry_ids:
            log(
                f"[QA] Blocking {len(blocked_entry_ids)} entries "
                "with QA errors."
            )

        entries_by_file: Dict[str, List[Any]] = {}

        for entry in entries:
            if entry.ignored:
                continue

            if entry.id in blocked_entry_ids:
                continue

            if not entry.target_text:
                continue

            if entry.status in (
                EntryStatus.PENDING,
                EntryStatus.ERROR,
            ):
                continue

            entries_by_file.setdefault(entry.file_path, []).append(entry)

        if not entries_by_file:
            raise RuntimeError(
                "No entries available for patch generation."
            )

        file_stats: List[tuple[str, int]] = []

        for rel_path, file_entries in entries_by_file.items():
            original_file = game_root / rel_path

            if not original_file.exists():
                log(
                    f"[WARN] Original file missing, skipped: {rel_path}"
                )
                continue

            output_file = patch_dir / rel_path

            changed = handler.inject_file(
                file_path=str(original_file),
                entries=file_entries,
                output_path=str(output_file),
            )

            file_stats.append((rel_path, changed))
            log(f"{rel_path}: injected {changed} strings")

        if not file_stats:
            raise RuntimeError(
                "Patch generation failed: no files were written."
            )

        manifest = {
            "tool": "AutoGame Localizer",
            "version": __version__,
            "project_id": store.meta.get("project_id"),
            "project_name": store.meta.get("name"),
            "engine": store.meta.get("engine"),
            "game_path": store.meta.get("game_path"),
            "target_language": store.meta.get("target_language"),
            "created_at": _now_iso(),
            "files": [],
        }

        for rel_path, changed in file_stats:
            original_file = game_root / rel_path
            patched_file = patch_dir / rel_path

            manifest["files"].append(
                {
                    "original_path": rel_path,
                    "patched_path": rel_path,
                    "original_sha256": (
                        sha256_file(original_file)
                        if original_file.exists()
                        else None
                    ),
                    "patched_sha256": (
                        sha256_file(patched_file)
                        if patched_file.exists()
                        else None
                    ),
                    "changed_entries": changed,
                }
            )

        manifest_path = patch_dir / "manifest.json"

        atomic_write_text(
            manifest_path,
            __import__("json").dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
        )

        store.save_meta()

        log("-" * 60)
        log(f"Patch created: {patch_dir}")
        log(f"Manifest: {manifest_path}")

        return {
            "patch_dir": str(patch_dir.resolve()),
            "files": len(file_stats),
        }


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------

def install_project(
    project_dir: Path | str,
    patch_path: Path | str,
    force: bool = False,
    backup: bool = True,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    backup_id, installed = install_patch(
        project_dir=project_dir,
        patch_dir=patch_path,
        force=force,
        backup=backup,
    )

    log(f"Installed {len(installed)} file(s).")
    log(f"Backup ID: {backup_id}")

    return {
        "backup_id": backup_id,
        "installed": len(installed),
        "files": installed,
    }


# ----------------------------------------------------------------------
# Rollback
# ----------------------------------------------------------------------

def rollback_project(
    project_dir: Path | str,
    backup_id: Optional[str] = None,
    force: bool = False,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    used_backup_id, restored, deleted = rollback(
        project_dir=project_dir,
        backup_id=backup_id,
        force=force,
    )

    log(f"Rollback complete.")
    log(f"Backup ID: {used_backup_id}")
    log(f"Restored {len(restored)} file(s).")
    log(f"Deleted {len(deleted)} file(s) added by patch.")

    return {
        "backup_id": used_backup_id,
        "restored": len(restored),
        "deleted": len(deleted),
    }


# ----------------------------------------------------------------------
# Backups
# ----------------------------------------------------------------------

def list_backups_project(project_dir: Path | str) -> List[Dict[str, Any]]:
    return list_backups(project_dir)


def preview_patch_project(
    project_dir: Path | str,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    """
    Preview what patch generation would do without writing files.
    """
    log = _make_log(log)

    with ProjectStore(project_dir) as store:
        entries = store.all_entries()

        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary = load_glossary(glossary_path)

        qa_results, qa_stats = run_qa_entries(
            entries=entries,
            target_language=store.meta.get("target_language", ""),
            glossary=glossary,
        )

        blocked_entry_ids = {
            result.entry_id
            for result in qa_results
            if result.errors
        }

        entries_by_file: Dict[str, List[Any]] = {}

        for entry in entries:
            if entry.ignored:
                continue

            if entry.id in blocked_entry_ids:
                continue

            if not entry.target_text:
                continue

            if entry.status in (
                EntryStatus.PENDING,
                EntryStatus.ERROR,
            ):
                continue

            entries_by_file.setdefault(entry.file_path, []).append(entry)

        files: List[Dict[str, Any]] = []

        for rel_path, file_entries in sorted(entries_by_file.items()):
            samples = []

            for entry in file_entries[:5]:
                samples.append(
                    {
                        "source": entry.source_text,
                        "target": entry.target_text,
                    }
                )

            files.append(
                {
                    "file_path": rel_path,
                    "entry_count": len(file_entries),
                    "samples": samples,
                }
            )

        log(
            f"Patch preview: files={len(files)}, "
            f"blocked_error_entries={len(blocked_entry_ids)}"
        )

        return {
            "total_entries": len(entries),
            "patchable_files": len(files),
            "blocked_error_entries": len(blocked_entry_ids),
            "qa_summary": qa_stats,
            "files": files,
        }
