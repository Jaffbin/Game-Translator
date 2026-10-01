from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..models import EntryStatus
from ..project import ProjectStore
from ..qa import (
    load_glossary,
    run_qa_entries,
    summarize_issues,
)


LogFunc = Callable[[str], None]


def _make_log(log: Optional[LogFunc]) -> LogFunc:
    if log is None:
        return lambda message: None
    return log


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


def list_project_glossary(
    project_dir: Path | str,
    query: str = "",
) -> List[Dict[str, Any]]:
    """List project glossary entries from glossary.csv without exposing filesystem paths."""
    path = Path(project_dir) / "glossary.csv"
    entries = load_glossary(path)
    q = (query or "").strip().lower()
    items = []
    for entry in entries:
        if q and q not in entry.source_term.lower() and q not in entry.target_term.lower():
            continue
        items.append({
            "source_term": entry.source_term,
            "target_term": entry.target_term,
            "level": entry.level,
            "case_sensitive": entry.case_sensitive,
        })
    return items


def upsert_project_glossary(
    project_dir: Path | str,
    source_term: str,
    target_term: str,
    level: str = "required",
    case_sensitive: bool = False,
) -> Dict[str, Any]:
    """Create or update one glossary term; the project stores a portable CSV."""
    source_term = (source_term or "").strip()
    target_term = (target_term or "").strip()
    level = (level or "required").strip().lower()
    if not source_term:
        raise ValueError("Source term is required.")
    if not target_term:
        raise ValueError("Target term is required.")
    if level not in {"required", "preferred"}:
        raise ValueError("Level must be required or preferred.")

    path = Path(project_dir) / "glossary.csv"
    current = list_project_glossary(project_dir)
    updated = False
    for item in current:
        if item["source_term"].casefold() == source_term.casefold():
            item.update({
                "source_term": source_term,
                "target_term": target_term,
                "level": level,
                "case_sensitive": bool(case_sensitive),
            })
            updated = True
            break
    if not updated:
        current.append({
            "source_term": source_term,
            "target_term": target_term,
            "level": level,
            "case_sensitive": bool(case_sensitive),
        })

    current.sort(key=lambda x: x["source_term"].casefold())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source_term", "target_term", "level", "case_sensitive"],
        )
        writer.writeheader()
        writer.writerows(current)
    return {
        "updated": updated,
        "entry": {
            "source_term": source_term,
            "target_term": target_term,
            "level": level,
            "case_sensitive": bool(case_sensitive),
        },
        "count": len(current),
    }


def delete_project_glossary(
    project_dir: Path | str,
    source_term: str,
) -> bool:
    """Delete a glossary term by source term. Returns whether a row was removed."""
    source_term = (source_term or "").strip()
    if not source_term:
        raise ValueError("Source term is required.")
    path = Path(project_dir) / "glossary.csv"
    current = list_project_glossary(project_dir)
    filtered = [x for x in current if x["source_term"].casefold() != source_term.casefold()]
    removed = len(filtered) != len(current)
    if not removed:
        return False
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source_term", "target_term", "level", "case_sensitive"],
        )
        writer.writeheader()
        writer.writerows(filtered)
    return True
