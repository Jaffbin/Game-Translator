from __future__ import annotations
from agl.qa import load_glossary, run_qa_entries

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from agl.cache import TranslationMemory
from agl.config import load_config
from agl.engines import detect_handler, get_handler_by_id
from agl.models import EntryStatus
from agl.pipeline import translate_one
from agl.placeholders import PlaceholderError
from agl.project import ProjectStore
from agl.providers import TranslationProviderError, create_provider


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# init
# ----------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    game_root = Path(args.game_path).resolve()

    handler = detect_handler(str(game_root))

    if handler is None:
        sys.exit(f"Unsupported or unknown game engine: {game_root}")

    try:
        with ProjectStore.create(
            project_dir=args.project_dir,
            game_path=game_root,
            engine_id=handler.engine_id,
            target_language=args.target_language,
            name=args.name,
        ) as store:
            print(f"Project created: {store.project_dir}")
            print(f"Engine: {handler.engine_id}")
            print(f"Game path: {game_root}")
            print(f"Target language: {args.target_language}")
    except FileExistsError as exc:
        sys.exit(str(exc))


# ----------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> None:
    with ProjectStore(args.project_dir) as store:
        handler = get_handler_by_id(store.meta["engine"])

        if handler is None:
            sys.exit(f"No handler found for engine: {store.meta['engine']}")

        game_path = store.meta["game_path"]

        if not handler.detect(game_path):
            sys.exit(
                f"Game path is not valid for engine {handler.engine_id}: "
                f"{game_path}"
            )

        files = handler.find_text_files(game_path)

        if not files:
            sys.exit("No translatable files found.")

        total = 0

        for file_path in files:
            rel_path = os.path.relpath(file_path, game_path)

            try:
                entries = handler.extract_file(file_path, game_path)
            except Exception as exc:
                print(f"[ERROR] Failed to extract {rel_path}: {exc}")
                continue

            store.upsert_entries(entries)
            total += len(entries)

            print(f"{rel_path}: extracted {len(entries)} entries")

        store.save_meta()

        print("-" * 70)
        print(f"Scan complete. Total entries extracted: {total}")
        print("Existing translations and review states were preserved.")

        if total == 0 and store.meta.get("engine") == "renpy":
            print()
            print("[INFO] Ren'Py: no old/new translation templates found.")
            print("[INFO] Phase 3 currently translates Ren'Py translation templates.")
            print("[INFO] Generate templates with Ren'Py SDK first, for example:")
            print("[INFO]   renpy.exe <project> translate chinese")
            print("[INFO] or:")
            print("[INFO]   python renpy.py <project> translate chinese")
            print("[INFO] Then run scan again.")

# ----------------------------------------------------------------------
# translate
# ----------------------------------------------------------------------

def cmd_translate(args: argparse.Namespace) -> None:
    config = load_config()

    with ProjectStore(args.project_dir) as store:
        provider_id = args.provider or config.first_available_provider()
        provider_config = config.get_provider(provider_id)

        if provider_config is None:
            sys.exit(f"Provider not found: {provider_id}")

        provider = create_provider(
            provider_config,
            model_override=args.model,
        )

        cache_namespace = f"{provider.id}:{provider.model}"
        target_language = (
            store.meta.get("target_language")
            or config.target_language
        )

        print(f"Provider: {provider.id}")
        print(f"Model: {provider.model}")
        print(f"Target language: {target_language}")
        print(f"Cache namespace: {cache_namespace}")
        print("-" * 70)

        if provider.id == "mock":
            print(
                "[WARN] Using mock provider. "
                "Translations will be fake placeholders."
            )

        cache = TranslationMemory(config.cache_db)

        entries = store.all_entries()

        translated = 0
        skipped = 0
        failed = 0

        for entry in entries:
            if entry.locked or entry.ignored:
                skipped += 1
                continue

            if entry.human_reviewed and not args.retranslate:
                skipped += 1
                continue

            if entry.status == EntryStatus.REVIEWED and not args.retranslate:
                skipped += 1
                continue

            if (
                entry.status == EntryStatus.MACHINE_TRANSLATED
                and not args.retranslate
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
                    use_cache=not args.no_cache,
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

            except (TranslationProviderError, PlaceholderError) as exc:
                store.update_entry(
                    entry.id,
                    status=EntryStatus.ERROR,
                    note=str(exc)[:1000],
                )

                failed += 1
                print(f"[ERROR] {entry.id}: {exc}")

        cache.close()
        store.save_meta()

        print("-" * 70)
        print(f"Translated: {translated}")
        print(f"Skipped: {skipped}")
        print(f"Failed: {failed}")


# ----------------------------------------------------------------------
# export
# ----------------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> None:
    with ProjectStore(args.project_dir) as store:
        count = store.export_csv(
            output_path=args.output,
            status=args.status,
        )

        print(f"Exported {count} entries to {args.output}")


# ----------------------------------------------------------------------
# import
# ----------------------------------------------------------------------

def cmd_import(args: argparse.Namespace) -> None:
    with ProjectStore(args.project_dir) as store:
        stats = store.import_csv(
            input_path=args.input,
            overwrite_locked=args.overwrite_locked,
        )

        store.save_meta()

        print("CSV import complete.")
        print(f"Updated:   {stats['updated']}")
        print(f"Unchanged: {stats['unchanged']}")
        print(f"Missing:   {stats['missing']}")
        print(f"Locked:    {stats['locked']}")
        print(f"Invalid:   {stats['invalid']}")


# ----------------------------------------------------------------------
# patch
# ----------------------------------------------------------------------

def cmd_patch(args: argparse.Namespace) -> None:
    with ProjectStore(args.project_dir) as store:
        handler = get_handler_by_id(store.meta["engine"])

        if handler is None:
            sys.exit(f"No handler found for engine: {store.meta['engine']}")

        game_root = Path(store.meta["game_path"])

        if not handler.detect(str(game_root)):
            sys.exit(
                f"Game path is not valid for engine {handler.engine_id}: "
                f"{game_root}"
            )

        output_root = Path(args.output)
        output_root.mkdir(parents=True, exist_ok=True)

        entries = store.all_entries()

        # ----------------------------------------------------------
        # Phase 6 QA gate
        # ----------------------------------------------------------

        glossary_path = Path(args.project_dir) / "glossary.csv"
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
            print(
                f"[QA] Blocking {len(blocked_entry_ids)} entries with QA errors. "
                "Run phase6_qa.py check for details."
            )

        entries_by_file: Dict[str, List] = {}

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
            sys.exit("No translatable entries found for patch generation.")

        file_stats = []

        for rel_path, file_entries in entries_by_file.items():
            original_file = game_root / rel_path

            if not original_file.exists():
                print(
                    f"[WARN] Original file missing, skipped: {rel_path}"
                )
                continue

            output_file = output_root / rel_path

            changed = handler.inject_file(
                file_path=str(original_file),
                entries=file_entries,
                output_path=str(output_file),
            )

            file_stats.append((rel_path, changed))

            print(f"{rel_path}: injected {changed} strings")

        manifest = {
            "tool": "AutoGame Localizer",
            "version": "0.2.0",
            "project_id": store.meta.get("project_id"),
            "project_name": store.meta.get("name"),
            "engine": store.meta.get("engine"),
            "game_path": store.meta.get("game_path"),
            "target_language": store.meta.get("target_language"),
            "created_at": now_iso(),
            "files": [],
        }

        for rel_path, changed in file_stats:
            original_file = game_root / rel_path
            patched_file = output_root / rel_path

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

        manifest_path = output_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        store.save_meta()

        print("-" * 70)
        print(f"Patch created: {output_root}")
        print(f"Manifest: {manifest_path}")


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> None:
    with ProjectStore(args.project_dir) as store:
        print("Project")
        print(f"  Name:            {store.meta.get('name')}")
        print(f"  Project ID:      {store.meta.get('project_id')}")
        print(f"  Engine:          {store.meta.get('engine')}")
        print(f"  Game path:       {store.meta.get('game_path')}")
        print(f"  Target language: {store.meta.get('target_language')}")
        print(f"  Created at:      {store.meta.get('created_at')}")
        print(f"  Updated at:      {store.meta.get('updated_at')}")
        print()

        counts = store.stats_by_status()
        total = sum(counts.values())

        print("Entries")
        print(f"  Total: {total}")

        for status_value, count in counts.items():
            print(f"  {status_value}: {count}")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2: project-based game translation workflow"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser(
        "init",
        help="Create a new translation project",
    )
    p_init.add_argument("project_dir")
    p_init.add_argument("--game-path", required=True)
    p_init.add_argument("--target-language", default="zh-CN")
    p_init.add_argument("--name", default=None)
    p_init.set_defaults(func=cmd_init)

    # scan
    p_scan = subparsers.add_parser(
        "scan",
        help="Scan game files and upsert entries into project",
    )
    p_scan.add_argument("project_dir")
    p_scan.set_defaults(func=cmd_scan)

    # translate
    p_translate = subparsers.add_parser(
        "translate",
        help="Translate pending entries",
    )
    p_translate.add_argument("project_dir")
    p_translate.add_argument("--provider", default=None)
    p_translate.add_argument("--model", default=None)
    p_translate.add_argument("--no-cache", action="store_true")
    p_translate.add_argument(
        "--retranslate",
        action="store_true",
        help="Retranslate already machine-translated or reviewed entries",
    )
    p_translate.set_defaults(func=cmd_translate)

    # export
    p_export = subparsers.add_parser(
        "export",
        help="Export entries to CSV",
    )
    p_export.add_argument("project_dir")
    p_export.add_argument("--output", required=True)
    p_export.add_argument(
        "--status",
        default=None,
        choices=[status.value for status in EntryStatus],
    )
    p_export.set_defaults(func=cmd_export)

    # import
    p_import = subparsers.add_parser(
        "import",
        help="Import reviewed CSV",
    )
    p_import.add_argument("project_dir")
    p_import.add_argument("--input", required=True)
    p_import.add_argument(
        "--overwrite-locked",
        action="store_true",
    )
    p_import.set_defaults(func=cmd_import)

    # patch
    p_patch = subparsers.add_parser(
        "patch",
        help="Generate translated patch",
    )
    p_patch.add_argument("project_dir")
    p_patch.add_argument("--output", default="Translated_Patch")
    p_patch.set_defaults(func=cmd_patch)

    # status
    p_status = subparsers.add_parser(
        "status",
        help="Show project status",
    )
    p_status.add_argument("project_dir")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()