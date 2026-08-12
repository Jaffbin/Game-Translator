from agl.cache import TranslationMemory
from agl.config import load_config
from agl.pipeline import translate_one
from agl.providers import TranslationProviderError, create_provider


def main() -> None:
    config = load_config()

    provider_id = config.first_available_provider()
    provider_config = config.get_provider(provider_id)

    if provider_config is None:
        raise RuntimeError(f"Provider not found: {provider_id}")

    provider = create_provider(provider_config)
    cache = TranslationMemory(config.cache_db)

    print(f"Using provider: {provider.id}")
    print(f"Using model: {provider.model}")
    print(f"Target language: {config.target_language}")
    print(f"Cache DB: {config.cache_db}")
    print("-" * 60)

    samples = [
        "You have \\v[1] gold.",
        "Hello [player_name], welcome to the dungeon.",
        "Obtained {item_name}.",
        "Attack",
        "Short Sword",
    ]

    for source_text in samples:
        try:
            translated = translate_one(
                provider=provider,
                cache=cache,
                source_text=source_text,
                target_language=config.target_language,
                context="Phase0 demo",
            )
            print(f"SRC: {source_text}")
            print(f"TGT: {translated}")
            print("-" * 60)

        except TranslationProviderError as exc:
            print(f"[PROVIDER ERROR] {exc}")
        except Exception as exc:
            print(f"[ERROR] {exc}")

    cache.close()


if __name__ == "__main__":
    main()