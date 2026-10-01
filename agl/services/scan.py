from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..io_utils import atomic_write_text
from ..engines import get_handler_by_id
from ..models import EntryStatus, TranslationEntry
from ..project import ProjectStore


LogFunc = Callable[[str], None]
CancelFunc = Callable[[], bool]
AUTO_REMOVED_NOTE = "[AUTO:SOURCE_REMOVED] Source location was not found during the latest scan."


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


def _entry_origin_key(entry: TranslationEntry) -> str:
    if entry.location:
        return ProjectStore.make_origin_key(entry.engine, entry.file_path, entry.location)
    return hashlib.sha256(
        "|".join(
            [entry.engine or "", entry.file_path or "", "source", entry.source_text or ""]
        ).encode("utf-8")
    ).hexdigest()[:32]


def scan_project(
    project_dir: Path | str,
    log: Optional[LogFunc] = None,
    should_cancel: Optional[CancelFunc] = None,
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

        total_entries = 0
        discovered_files = {
            _relative_path(file_path, game_path)
            for file_path in files
        }
        failed_files = set()
        seen_origins = set()
        processed_files = 0

        for file_path in files:
            if should_cancel is not None and should_cancel():
                log("Scan cancelled before reconciliation; existing project entries were preserved.")
                return {
                    "files": len(files),
                    "processed_files": processed_files,
                    "entries": total_entries,
                    "auto_ignored": 0,
                    "restored": 0,
                    "errors": len(failed_files),
                    "cancelled": True,
                }
            rel_path = _relative_path(file_path, game_path)

            try:
                entries = handler.extract_file(file_path, game_path)
            except Exception as exc:
                failed_files.add(rel_path)
                log(f"[ERROR] Failed to extract {rel_path}: {exc}")
                processed_files += 1
                continue

            store.upsert_entries(entries)
            seen_origins.update(_entry_origin_key(entry) for entry in entries)
            total_entries += len(entries)
            processed_files += 1

            log(f"{rel_path}: extracted {len(entries)} entries")

        auto_ignored = 0
        restored = 0
        for entry in store.all_entries():
            if entry.file_path in failed_files:
                continue
            present = (
                entry.file_path in discovered_files
                and _entry_origin_key(entry) in seen_origins
            )
            if not present:
                if not entry.ignored:
                    if store.update_entry(
                        entry.id,
                        status=EntryStatus.IGNORED,
                        ignored=True,
                        note=AUTO_REMOVED_NOTE,
                    ):
                        auto_ignored += 1
                continue
            if entry.ignored and entry.note == AUTO_REMOVED_NOTE:
                next_status = (
                    EntryStatus.NEEDS_REVIEW
                    if (entry.target_text or "").strip()
                    else EntryStatus.PENDING
                )
                if store.update_entry(
                    entry.id,
                    status=next_status,
                    ignored=False,
                    fuzzy=bool((entry.target_text or "").strip()),
                    note="",
                ):
                    restored += 1

        store.save_meta()

        log("-" * 60)
        log(f"Scan complete. Total entries extracted: {total_entries}")
        if auto_ignored:
            log(f"Marked {auto_ignored} removed source entries as ignored.")
        if restored:
            log(f"Restored {restored} source entries that reappeared.")
        log("Existing translations and review states were preserved.")

        return {
            "files": len(files),
            "processed_files": processed_files,
            "entries": total_entries,
            "auto_ignored": auto_ignored,
            "restored": restored,
            "errors": len(failed_files),
            "cancelled": False,
        }


# ----------------------------------------------------------------------
# Update sync preview
# ----------------------------------------------------------------------

def _sync_state_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / "sync_decisions.json"


def _load_sync_decisions(project_dir: Path | str) -> Dict[str, Dict[str, Any]]:
    path = _sync_state_path(project_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_sync_decisions(project_dir: Path | str, decisions: Dict[str, Dict[str, Any]]) -> None:
    path = _sync_state_path(project_dir)
    atomic_write_text(path, json.dumps(decisions, ensure_ascii=False, indent=2))


def _sync_fingerprint(kind: str, item: Dict[str, Any]) -> str:
    if kind in {"added", "changed"}:
        return str(item.get("source_after") or "")
    return str(item.get("source_before") or "")


def preview_update_sync(
    project_dir: Path | str,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    """Scan the game without modifying the project and report actionable added/changed/removed entries."""
    log = _make_log(log)
    with ProjectStore(project_dir) as store:
        handler = get_handler_by_id(store.meta["engine"])
        if handler is None:
            raise RuntimeError(f"No handler found for engine: {store.meta['engine']}")
        game_path = store.meta["game_path"]
        if not handler.detect(game_path):
            raise RuntimeError(f"Game path is not valid for engine {handler.engine_id}: {game_path}")

        current = {}
        for entry in store.all_entries():
            key = ProjectStore.make_origin_key(entry.engine, entry.file_path, entry.location)
            current[key] = entry

        files = handler.find_text_files(game_path)
        incoming = {}
        errors = []
        for file_path in files:
            rel_path = _relative_path(file_path, game_path)
            try:
                extracted = handler.extract_file(file_path, game_path)
            except Exception as exc:
                errors.append({"file_path": rel_path, "message": str(exc)[:300]})
                log(f"[ERROR] Failed to extract {rel_path}: {exc}")
                continue
            for entry in extracted:
                key = ProjectStore.make_origin_key(entry.engine, entry.file_path, entry.location)
                incoming[key] = entry

        added=[]; changed=[]; unchanged=[]; removed=[]
        for key, entry in incoming.items():
            old = current.get(key)
            if old is None:
                added.append({
                    "kind": "added", "entry_id": entry.id, "origin_key": key,
                    "file_path": entry.file_path, "engine": entry.engine,
                    "location": entry.location, "context": entry.context, "note": entry.note,
                    "source_after": entry.source_text, "status": entry.status.value,
                })
            elif old.source_text != entry.source_text:
                changed.append({
                    "kind": "changed", "entry_id": old.id, "origin_key": key,
                    "file_path": entry.file_path, "engine": entry.engine,
                    "location": entry.location, "context": entry.context, "note": entry.note,
                    "source_before": old.source_text, "source_after": entry.source_text,
                    "target_text": old.target_text or "", "status": old.status.value, "fuzzy": True,
                })
            else:
                unchanged.append(entry.id)
        for key, old in current.items():
            if key not in incoming:
                removed.append({
                    "kind": "removed", "entry_id": old.id, "origin_key": key,
                    "file_path": old.file_path, "engine": old.engine, "location": old.location,
                    "source_before": old.source_text, "status": old.status.value,
                })

        persisted = _load_sync_decisions(project_dir)
        actionable = []
        suppressed = 0
        for item in added + changed + removed:
            prior = persisted.get(item["origin_key"])
            if prior and prior.get("fingerprint") == _sync_fingerprint(item["kind"], item):
                suppressed += 1
                continue
            actionable.append(item)

        log(f"[Sync] added={sum(x['kind']=='added' for x in actionable)} changed={sum(x['kind']=='changed' for x in actionable)} unchanged={len(unchanged)} removed={sum(x['kind']=='removed' for x in actionable)} suppressed={suppressed}")
        return {
            "files": len(files),
            "summary": {
                "added": sum(x["kind"] == "added" for x in actionable),
                "changed": sum(x["kind"] == "changed" for x in actionable),
                "unchanged": len(unchanged),
                "removed": sum(x["kind"] == "removed" for x in actionable),
                "errors": len(errors),
                "suppressed": suppressed,
            },
            "items": actionable[:200],
            "errors": errors[:20],
        }


def apply_update_sync(
    project_dir: Path | str,
    decisions: List[Dict[str, Any]],
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    """Apply explicit sync decisions against a fresh game scan. Never mutates game files directly."""
    log = _make_log(log)
    allowed = {"keep", "ignore", "retranslate", "review"}
    unique: Dict[str, str] = {}
    for decision in decisions or []:
        key = str(decision.get("origin_key") or "").strip()
        action = str(decision.get("action") or "").strip().lower()
        if key and action in allowed:
            unique[key] = action
    if not unique:
        raise ValueError("No valid sync decisions supplied.")

    preview = preview_update_sync(project_dir, log=log)
    by_key = {item["origin_key"]: item for item in preview["items"]}
    applied = ignored = skipped = 0
    added = changed = reviewed = retranslated = 0
    checkpoint = None
    persisted = _load_sync_decisions(project_dir)

    with ProjectStore(project_dir) as store:
        if any(unique[key] in {"keep", "retranslate", "review"} and key in by_key for key in unique):
            checkpoint = store.create_revision("Before Update Sync")

        current_by_origin = {
            ProjectStore.make_origin_key(e.engine, e.file_path, e.location): e
            for e in store.all_entries()
        }
        for origin_key, action in unique.items():
            item = by_key.get(origin_key)
            if item is None:
                skipped += 1
                continue

            kind = item["kind"]
            fingerprint = _sync_fingerprint(kind, item)
            entry = current_by_origin.get(origin_key)

            if action == "ignore":
                persisted[origin_key] = {"decision": "ignore", "kind": kind, "fingerprint": fingerprint, "updated_at": _now_iso()}
                ignored += 1
                continue

            if kind == "added":
                incoming = TranslationEntry(
                    id=item["entry_id"], source_text=item["source_after"], target_text=None,
                    context=item.get("context", ""), file_path=item["file_path"], engine=item.get("engine", ""),
                    location=item.get("location") or {}, status=EntryStatus.PENDING, note=item.get("note", ""),
                )
                store.upsert_entries([incoming])
                applied += 1; added += 1
                if action == "review":
                    reviewed += 1
                continue

            if kind == "changed":
                if entry is None:
                    skipped += 1
                    continue
                incoming = TranslationEntry(
                    id=entry.id, source_text=item["source_after"], target_text=entry.target_text,
                    context=item.get("context", entry.context), file_path=item["file_path"], engine=entry.engine,
                    location=item.get("location") or entry.location, status=EntryStatus.NEEDS_REVIEW,
                    locked=entry.locked, ignored=False, fuzzy=True, machine_translated=entry.machine_translated,
                    human_reviewed=False, note=item.get("note", entry.note),
                )
                store.upsert_entries([incoming])
                if action == "retranslate":
                    store.update_entry(entry.id, target_text="", status=EntryStatus.PENDING, fuzzy=True, machine_translated=False, human_reviewed=False)
                    retranslated += 1
                else:
                    store.update_entry(entry.id, status=EntryStatus.NEEDS_REVIEW, fuzzy=True, machine_translated=False, human_reviewed=False)
                    if action == "review":
                        reviewed += 1
                applied += 1; changed += 1
                continue

            if kind == "removed":
                # Never delete a user's translation implicitly. Keep/Review both leave the project entry intact.
                persisted[origin_key] = {"decision": action, "kind": kind, "fingerprint": fingerprint, "updated_at": _now_iso()}
                applied += 1
                if action == "review":
                    reviewed += 1
                continue

        store.save_meta()

    _save_sync_decisions(project_dir, persisted)
    log(f"[Sync] applied={applied} added={added} changed={changed} ignored={ignored} retranslated={retranslated} reviewed={reviewed} skipped={skipped}")
    return {
        "applied": applied, "ignored": ignored, "skipped": skipped,
        "added": added, "changed": changed, "retranslated": retranslated, "reviewed": reviewed,
        "checkpoint": checkpoint,
    }
