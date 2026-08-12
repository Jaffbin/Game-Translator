from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .cache import TranslationMemory
from .config import load_config
from .engines import get_handler_by_id
from .installer import (
    install_patch,
    list_backups,
    rollback,
    sha256_file,
)
from .models import EntryStatus
from .pipeline import translate_one
from .project import ProjectStore
from .providers import create_provider
from .qa import (
    load_glossary,
    run_qa_entries,
    summarize_issues,
)


LogFunc = Callable[[str], None]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _make_log(log: Optional[LogFunc]) -> LogFunc:
    if log is None:
        return lambda message: None
    return log


def _relative_path(file_path: Path | str, root: Path | str) -> str:
    file_path = Path(file_path)
    root = Path(root)

    try:
        return file_path.relative_to(root).as_posix()
    except ValueError:
        return file_path.name


# ----------------------------------------------------------------------
# Project info
# ----------------------------------------------------------------------

def get_project_info(project_dir: Path | str) -> Dict[str, Any]:
    with ProjectStore(project_dir) as store:
        return {
            "meta": store.meta,
            "stats": store.stats_by_status(),
        }


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------

def scan_project(
    project_dir: Path | str,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    with ProjectStore(project_dir) as store:
        handler = get_handler_by_id(store.meta["engine"])

        if handler is None:
            raise RuntimeError(
                f"No handler found for engine: {store.meta['engine']}"
            )

        game_path = store.meta["game_path"]

        if not handler.detect(game_path):
            raise RuntimeError(
                f"Game path is not valid for engine {handler.engine_id}: "
                f"{game_path}"
            )

        files = handler.find_text_files(game_path)

        if not files:
            log("No translatable files found.")
            return {
                "files": 0,
                "entries": 0,
            }

        total_entries = 0

        for file_path in files:
            rel_path = _relative_path(file_path, game_path)

            try:
                entries = handler.extract_file(file_path, game_path)
            except Exception as exc:
                log(f"[ERROR] Failed to extract {rel_path}: {exc}")
                continue

            store.upsert_entries(entries)
            total_entries += len(entries)

            log(f"{rel_path}: extracted {len(entries)} entries")

        store.save_meta()

        log("-" * 60)
        log(f"Scan complete. Total entries extracted: {total_entries}")
        log("Existing translations and review states were preserved.")

        return {
            "files": len(files),
            "entries": total_entries,
        }


# ----------------------------------------------------------------------
# Translate
# ----------------------------------------------------------------------

def translate_project(
    project_dir: Path | str,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
    retranslate: bool = False,
    no_cache: bool = False,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    config = load_config()

    with ProjectStore(project_dir) as store:
        provider_id = provider_id or config.first_available_provider()
        provider_config = config.get_provider(provider_id)

        if provider_config is None:
            raise RuntimeError(f"Provider not found: {provider_id}")

        provider = create_provider(
            provider_config,
            model_override=model or None,
        )

        cache_namespace = f"{provider.id}:{provider.model}"
        target_language = (
            store.meta.get("target_language")
            or config.target_language
        )

        log(f"Provider: {provider.id}")
        log(f"Model: {provider.model}")
        log(f"Target language: {target_language}")
        log(f"Cache namespace: {cache_namespace}")

        if provider.id == "mock":
            log(
                "[WARN] Using mock provider. "
                "Translations will be fake placeholders."
            )

        cache = TranslationMemory(config.cache_db)

        translated = 0
        skipped = 0
        failed = 0

        try:
            entries = store.all_entries()
            total = len(entries)

            for index, entry in enumerate(entries, start=1):
                if entry.locked or entry.ignored:
                    skipped += 1
                    continue

                if entry.human_reviewed and not retranslate:
                    skipped += 1
                    continue

                if entry.status == EntryStatus.REVIEWED and not retranslate:
                    skipped += 1
                    continue

                if (
                    entry.status == EntryStatus.MACHINE_TRANSLATED
                    and not retranslate
                ):
                    skipped += 1
                    continue

                try:
                    translated_text = translate_one(
                        provider=provider,
                        cache=cache,
                        source_text=entry.source_text,
                        target_language=target_language,
                        context=entry.context,
                        use_cache=not no_cache,
                        cache_namespace=cache_namespace,
                    )

                    store.update_entry(
                        entry.id,
                        target_text=translated_text,
                        status=EntryStatus.MACHINE_TRANSLATED,
                        machine_translated=True,
                        human_reviewed=False,
                        note="",
                    )

                    translated += 1

                except Exception as exc:
                    store.update_entry(
                        entry.id,
                        status=EntryStatus.ERROR,
                        note=str(exc)[:1000],
                    )

                    failed += 1
                    log(f"[ERROR] {entry.id}: {exc}")

                if index % 50 == 0:
                    log(
                        f"Progress {index}/{total} "
                        f"translated={translated} "
                        f"skipped={skipped} "
                        f"failed={failed}"
                    )

        finally:
            cache.close()

        store.save_meta()

        log("-" * 60)
        log(f"Translated: {translated}")
        log(f"Skipped: {skipped}")
        log(f"Failed: {failed}")

        return {
            "translated": translated,
            "skipped": skipped,
            "failed": failed,
        }


# ----------------------------------------------------------------------
# QA
# ----------------------------------------------------------------------

def run_qa_project(
    project_dir: Path | str,
    apply: bool = False,
    apply_warnings: bool = False,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    with ProjectStore(project_dir) as store:
        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary = load_glossary(glossary_path)

        entries = store.all_entries()

        results, stats = run_qa_entries(
            entries=entries,
            target_language=store.meta.get("target_language", ""),
            glossary=glossary,
        )

        log(
            f"QA total={stats['total_entries']} "
            f"passed={stats['passed_entries']} "
            f"failed={stats['failed_entries']} "
            f"errors={stats['entries_with_errors']} "
            f"warnings={stats['entries_with_warnings']}"
        )

        if apply:
            entries_by_id = {entry.id: entry for entry in entries}
            updated = 0

            for result in results:
                entry = entries_by_id.get(result.entry_id)

                if entry is None:
                    continue

                if entry.locked or entry.ignored:
                    continue

                note = summarize_issues(result)

                if result.errors:
                    store.update_entry(
                        entry.id,
                        status=EntryStatus.ERROR,
                        note=note,
                    )
                    updated += 1

                elif result.warnings and apply_warnings:
                    if entry.status not in (
                        EntryStatus.REVIEWED,
                        EntryStatus.LOCKED,
                    ):
                        store.update_entry(
                            entry.id,
                            status=EntryStatus.NEEDS_REVIEW,
                            note=note,
                        )
                        updated += 1

            store.save_meta()
            stats["updated_entries"] = updated
            log(f"Updated {updated} entries from QA results.")

        return stats


# ----------------------------------------------------------------------
# Patch list
# ----------------------------------------------------------------------

def list_patches(project_dir: Path | str) -> List[Dict[str, str]]:
    project_dir = Path(project_dir)
    patches_dir = project_dir / "patches"

    if not patches_dir.exists():
        return []

    result: List[Dict[str, str]] = []

    for patch_dir in sorted(patches_dir.iterdir(), reverse=True):
        if not patch_dir.is_dir():
            continue

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
                    "%Y%m%d_%H%M%S"
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
            "version": "0.7.5",
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

        manifest_path.write_text(
            __import__("json").dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
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

# ----------------------------------------------------------------------
# Phase 10: CSV export / import / provider config / patch preview
# ----------------------------------------------------------------------

def export_project_csv(
    project_dir: Path | str,
    status: Optional[str] = None,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    if status is not None and not str(status).strip():
        status = None

    with ProjectStore(project_dir) as store:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        exports_dir = Path(store.project_dir) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        output_path = exports_dir / f"export_{timestamp}.csv"

        count = store.export_csv(
            output_path=output_path,
            status=status,
        )

        log(f"Exported {count} entries to {output_path}")

        return {
            "path": str(output_path.resolve()),
            "count": count,
        }


def import_project_csv(
    project_dir: Path | str,
    csv_path: Path | str,
    overwrite_locked: bool = False,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    with ProjectStore(project_dir) as store:
        stats = store.import_csv(
            input_path=csv_path,
            overwrite_locked=overwrite_locked,
        )

        store.save_meta()

        log(
            f"CSV import complete. "
            f"updated={stats.get('updated', 0)} "
            f"unchanged={stats.get('unchanged', 0)} "
            f"missing={stats.get('missing', 0)} "
            f"locked={stats.get('locked', 0)} "
            f"invalid={stats.get('invalid', 0)}"
        )

        return stats


def get_provider_configs() -> List[Dict[str, Any]]:
    """
    Return provider list from config.toml / environment.

    Never expose API keys.
    """
    config = load_config()

    items: List[Dict[str, Any]] = []

    for provider_id, provider_config in config.providers.items():
        items.append(
            {
                "id": provider_id,
                "type": provider_config.type,
                "model": provider_config.model,
                "api_key_env": provider_config.api_key_env,
                "has_api_key": bool(provider_config.api_key),
            }
        )

    return items


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