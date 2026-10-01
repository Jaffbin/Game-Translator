from __future__ import annotations

from typing import Dict, Optional

from .cache import TranslationMemory
from .placeholders import PlaceholderError, PlaceholderProtector
from .providers import TranslationProvider, TranslationProviderError


class TranslationQAError(RuntimeError):
    pass


_default_protector = PlaceholderProtector()


def translate_one(
    provider: TranslationProvider,
    cache: TranslationMemory,
    source_text: str,
    target_language: str,
    context: str = "",
    glossary: Optional[Dict[str, str]] = None,
    use_cache: bool = True,
    cache_namespace: str = "",
) -> str:
    """
    Translation pipeline:

      source_text
        -> cache lookup
        -> placeholder protection
        -> provider translation
        -> placeholder validation
        -> placeholder restoration
        -> cache save

    cache_namespace is used to separate cache by provider/model.
    Example:
      nvidia:meta/llama-3.1-8b-instruct
    """
    original = source_text or ""

    if not original.strip():
        return ""

    if use_cache:
        reviewed = cache.get_human_reviewed(original, target_language)
        if reviewed is not None:
            return reviewed
        cached = cache.get(
            original,
            target_language,
            namespace=cache_namespace,
        )
        if cached is not None:
            return cached

    protected_text, placeholders = _default_protector.protect(original)

    try:
        translated_protected = provider.translate(
            text=protected_text,
            target_language=target_language,
            context=context,
            glossary=glossary,
        )
    except TranslationProviderError:
        raise

    translated_protected = (translated_protected or "").strip()

    if not _default_protector.validate(translated_protected, placeholders):
        raise PlaceholderError(
            "Placeholder validation failed. "
            f"source={original!r}, "
            f"protected={protected_text!r}, "
            f"translated_protected={translated_protected!r}"
        )

    translated = _default_protector.restore(translated_protected, placeholders)

    if not translated.strip():
        raise TranslationQAError("Translation result is empty.")

    if use_cache:
        cache.put(
            source_text=original,
            target_language=target_language,
            translated_text=translated,
            provider=provider.id,
            model=provider.model,
            human_reviewed=False,
            namespace=cache_namespace,
        )

    return translated
