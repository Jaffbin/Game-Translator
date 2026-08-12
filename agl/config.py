from __future__ import annotations


import os
from dataclasses import dataclass, field
from pathlib import Path
from .workspace import app_root

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


def load_dotenv(path: Path | str = ".env") -> None:
    """
    Minimal .env loader.

    Supports:
      KEY=value
      KEY="value"
      KEY='value'

    Does not override existing environment variables.
    """
    path = Path(path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    type: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    timeout_seconds: int = 60
    max_retries: int = 3
    retry_backoff_seconds: float = 1.5
    temperature: float = 0.2

    @property
    def api_key(self) -> str:
        if not self.api_key_env:
            return ""
        return os.getenv(self.api_key_env, "")


@dataclass
class AppConfig:
    target_language: str = "zh-CN"
    cache_db: Path = Path(".cache/translation.db")
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        return self.providers.get(provider_id)

    def first_available_provider(self) -> str:
        """
        Return real provider if API key exists, otherwise fallback to mock.
        """
        for provider_id, provider_config in self.providers.items():
            if provider_id == "mock":
                continue
            if provider_config.type != "mock" and provider_config.api_key:
                return provider_id
        return "mock"


def _default_providers() -> dict[str, ProviderConfig]:
    return {
        "mock": ProviderConfig(
            id="mock",
            type="mock",
        ),
        "nvidia": ProviderConfig(
            id="nvidia",
            type="openai_compatible",
            base_url="https://integrate.api.nvidia.com/v1/chat/completions",
            model="meta/llama-3.1-8b-instruct",
            api_key_env="NVIDIA_API_KEY",
            timeout_seconds=60,
            max_retries=3,
            retry_backoff_seconds=1.5,
            temperature=0.2,
        ),
    }


def load_config(
    config_path: Path | str | None = None,
    env_path: Path | str | None = None,
) -> AppConfig:
    """
    Load config from environment and optional config.toml.

    Phase 9 behavior:

      - .env defaults to app_root()/.env
      - config.toml defaults to app_root()/config.toml
      - if config.toml does not exist but config.example.toml exists,
        use config.example.toml
      - relative cache_db paths are resolved against app_root()
    """
    root = app_root()

    if env_path is None:
        env_path = root / ".env"

    if config_path is None:
        candidates = [
            root / "config.toml",
            root / "config.example.toml",
        ]

        config_path = next(
            (path for path in candidates if path.exists()),
            root / "config.toml",
        )

    load_dotenv(env_path)

    providers = _default_providers()
    target_language = "zh-CN"
    cache_db = root / ".cache" / "translation.db"

    config_file = Path(config_path)

    if config_file.exists():
        if tomllib is None:
            print(
                "[WARN] config file found but tomllib/tomli is unavailable. "
                "Using default config and environment variables only."
            )
        else:
            data = tomllib.loads(config_file.read_text(encoding="utf-8"))

            target_language = data.get("translation", {}).get(
                "target_language",
                target_language,
            )

            cache_db_raw = data.get("project", {}).get("cache_db")
            if cache_db_raw:
                cache_db_path = Path(cache_db_raw)

                if cache_db_path.is_absolute():
                    cache_db = cache_db_path
                else:
                    cache_db = root / cache_db_path

            for provider_id, provider_data in data.get("providers", {}).items():
                providers[provider_id] = ProviderConfig(
                    id=provider_id,
                    type=provider_data.get("type", "openai_compatible"),
                    base_url=provider_data.get("base_url", ""),
                    model=provider_data.get("model", ""),
                    api_key_env=provider_data.get("api_key_env", ""),
                    timeout_seconds=int(provider_data.get("timeout_seconds", 60)),
                    max_retries=int(provider_data.get("max_retries", 3)),
                    retry_backoff_seconds=float(
                        provider_data.get("retry_backoff_seconds", 1.5)
                    ),
                    temperature=float(provider_data.get("temperature", 0.2)),
                )

    if "mock" not in providers:
        providers["mock"] = ProviderConfig(id="mock", type="mock")

    return AppConfig(
        target_language=target_language,
        cache_db=cache_db,
        providers=providers,
    )