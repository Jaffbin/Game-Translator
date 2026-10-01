from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..cache import TranslationMemory
from ..config import load_config
from ..models import EntryStatus, TranslationEntry
from ..pipeline import translate_one
from ..project import ProjectStore
from ..providers import TranslationProviderError, create_provider
from ..qa import load_glossary


LogFunc = Callable[[str], None]


def _make_log(log: Optional[LogFunc]) -> LogFunc:
    if log is None:
        return lambda message: None
    return log


def translate_project(
    project_dir: Path | str,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
    retranslate: bool = False,
    no_cache: bool = False,
    log: Optional[LogFunc] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[Dict[str, int]], None]] = None,
    stop_on_provider_error: bool = False,
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
        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary_entries = load_glossary(glossary_path)
        glossary = {item.source_term: item.target_term for item in glossary_entries if item.source_term}
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
        fatal_error = None

        try:
            entries = store.all_entries()
            total = len(entries)
            cancelled = False

            def report(processed: int) -> None:
                if progress is not None:
                    progress(
                        {
                            "processed": processed,
                            "total": total,
                            "translated": translated,
                            "skipped": skipped,
                            "failed": failed,
                        }
                    )

            report(0)

            for index, entry in enumerate(entries, start=1):
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    log(
                        f"Translation cancelled safely at {index - 1}/{total}. "
                        "Completed entries were preserved."
                    )
                    report(index - 1)
                    break

                if entry.locked or entry.ignored:
                    skipped += 1
                    report(index)
                    continue

                if entry.human_reviewed and not retranslate:
                    skipped += 1
                    report(index)
                    continue

                if entry.status == EntryStatus.REVIEWED and not retranslate:
                    skipped += 1
                    report(index)
                    continue

                if (
                    entry.status == EntryStatus.MACHINE_TRANSLATED
                    and not retranslate
                ):
                    skipped += 1
                    report(index)
                    continue

                try:
                    translated_text = translate_one(
                        provider=provider,
                        cache=cache,
                        source_text=entry.source_text,
                        target_language=target_language,
                        context=entry.context,
                        glossary=glossary,
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
                    if stop_on_provider_error and isinstance(exc, TranslationProviderError):
                        fatal_error = str(exc)
                        log("[ERROR] Provider stopped the automatic run; remaining entries were not sent.")
                        report(index)
                        break

                report(index)

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
            "cancelled": cancelled,
            "fatal_error": fatal_error,
        }


def search_translation_memory(
    query: str = "",
    target_language: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    config = load_config()
    with TranslationMemory(config.cache_db) as cache:
        return cache.search(query=query, target_language=target_language, limit=limit)


def remember_reviewed_translations(
    entries: List[TranslationEntry],
    target_language: str,
) -> int:
    """Promote human-reviewed project entries into shared translation memory."""
    config = load_config()
    remembered = 0
    with TranslationMemory(config.cache_db) as cache:
        for entry in entries:
            source = (entry.source_text or "").strip()
            target = (entry.target_text or "").strip()
            if not source or not target or not entry.human_reviewed:
                continue
            cache.put(
                source_text=source,
                target_language=target_language,
                translated_text=target,
                provider="human",
                model="reviewed",
                human_reviewed=True,
                namespace="",
            )
            remembered += 1
    return remembered


def suggest_entry_translation(
    project_dir: Path | str,
    entry_id: str,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
    force_ai: bool = False,
) -> Dict[str, Any]:
    """Return an exact TM hit when possible, otherwise generate a one-entry AI suggestion."""
    config = load_config()
    with ProjectStore(project_dir) as store:
        entry = store.get_entry(entry_id)
        if entry is None:
            raise ValueError("Entry not found.")
        target_language = store.meta.get("target_language") or config.target_language
        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary = {
            item.source_term: item.target_term
            for item in load_glossary(glossary_path)
            if item.source_term and item.target_term
        }
        source = entry.source_text or ""
        if not force_ai:
            with TranslationMemory(config.cache_db) as cache:
                memory = cache.get_exact_any(source, target_language)
            if memory:
                return {
                    "source": "translation_memory",
                    "suggestion": memory["translated_text"],
                    "provider": memory.get("provider", ""),
                    "model": memory.get("model", ""),
                    "human_reviewed": bool(memory.get("human_reviewed")),
                    "glossary_count": len(glossary),
                }

        provider_id = provider_id or config.first_available_provider()
        provider_config = config.get_provider(provider_id)
        if provider_config is None:
            raise ValueError(f"Provider not found: {provider_id}")
        provider = create_provider(provider_config, model_override=model or None)
        with TranslationMemory(config.cache_db) as cache:
            suggestion = translate_one(
                provider=provider,
                cache=cache,
                source_text=source,
                target_language=target_language,
                context=entry.context,
                glossary=glossary,
                use_cache=False,
            )
        return {
            "source": "ai",
            "suggestion": suggestion,
            "provider": provider.id,
            "model": provider.model,
            "human_reviewed": False,
            "glossary_count": len(glossary),
        }


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
                "ready": bool(
                    provider_id != "mock"
                    and provider_config.is_ready
                    and (
                        not provider_config.is_local_endpoint
                        or provider_config.local_endpoint_reachable()
                    )
                ),
            }
        )

    return items
