from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agl.cache import TranslationMemory
from agl.config import load_config
from agl.engines.rpgmaker import RPGMakerHandler
from agl.models import EntryStatus
from agl.pipeline import translate_one
from agl.placeholders import PlaceholderError
from agl.providers import TranslationProviderError, create_provider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1: RPG Maker MV/MZ safe translator"
    )

    parser.add_argument(
        "game_path",
        help="Path to RPG Maker game root",
    )

    parser.add_argument(
        "--output",
        default="Translated_Patch",
        help="Output patch directory",
    )

    parser.add_argument(
        "--provider",
        default=None,
        help="Provider id from config.toml, e.g. nvidia, mock",
    )

    parser.add_argument(
        "--model",
        default=None,
        help="Override model name, e.g. meta/llama-3.1-70b-instruct",
    )

    parser.add_argument(
        "--target-language",
        default=None,
        help="Target language, e.g. zh-CN",
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write translation cache",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract only; do not translate or write patch",
    )

    args = parser.parse_args()

    config = load_config()

    target_language = args.target_language or config.target_language

    provider_id = args.provider or config.first_available_provider()
    provider_config = config.get_provider(provider_id)

    if provider_config is None:
        sys.exit(f"Provider not found: {provider_id}")

    provider = create_provider(
        provider_config,
        model_override=args.model,
    )

    cache_namespace = f"{provider.id}:{provider.model}"

    print(f"Provider: {provider.id}")
    print(f"Model: {provider.model}")
    print(f"Target language: {target_language}")
    print(f"Cache namespace: {cache_namespace}")
    print("-" * 70)

    if provider.id == "mock" and not args.dry_run:
        print(
            "[WARN] You are using mock provider without --dry-run. "
            "Output will contain fake translations."
        )

    handler = RPGMakerHandler()
    game_root = Path(args.game_path).resolve()

    if not handler.detect(str(game_root)):
        sys.exit(f"Not detected as RPG Maker MV/MZ: {game_root}")

    files = handler.find_text_files(str(game_root))

    if not files:
        sys.exit("No RPG Maker JSON files found.")

    print(f"Found {len(files)} RPG Maker file(s).")

    cache = TranslationMemory(config.cache_db)

    total_entries = 0
    translated_entries = 0
    failed_entries = 0

    for file_path in files:
        rel_path = os.path.relpath(file_path, game_root)

        try:
            entries = handler.extract_file(file_path, str(game_root))
        except Exception as exc:
            print(f"[ERROR] Failed to extract {rel_path}: {exc}")
            continue

        total_entries += len(entries)
        print(f"{rel_path}: extracted {len(entries)} entries")

        if args.dry_run:
            continue

        for entry in entries:
            try:
                entry.target_text = translate_one(
                    provider=provider,
                    cache=cache,
                    source_text=entry.source_text,
                    target_language=target_language,
                    context=entry.context,
                    use_cache=not args.no_cache,
                    cache_namespace=cache_namespace,
                )

                entry.status = EntryStatus.MACHINE_TRANSLATED
                entry.machine_translated = True
                translated_entries += 1

            except (TranslationProviderError, PlaceholderError) as exc:
                entry.status = EntryStatus.ERROR
                entry.note = str(exc)
                failed_entries += 1
                print(f"[ERROR] {entry.id}: {exc}")

        output_path = Path(args.output) / rel_path
        changed = handler.inject_file(
            file_path=file_path,
            entries=entries,
            output_path=str(output_path),
        )

        print(f"  -> wrote {changed} translated strings to {output_path}")

    cache.close()

    print("-" * 70)
    print(f"Total entries: {total_entries}")

    if not args.dry_run:
        print(f"Translated: {translated_entries}")
        print(f"Failed: {failed_entries}")

    print("Done.")


if __name__ == "__main__":
    main()