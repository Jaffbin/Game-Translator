from __future__ import annotations


import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from .workspace import app_root, secrets_env_path
from .secrets import get_secret

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
        return get_secret(self.api_key_env) or ""

    @property
    def is_local_endpoint(self) -> bool:
        try:
            return urlparse(self.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
        except ValueError:
            return False

    @property
    def is_ready(self) -> bool:
        return bool(
            self.type == "openai_compatible"
            and self.base_url
            and self.model
            and (self.api_key or self.is_local_endpoint)
        )

    def local_endpoint_reachable(self, timeout_seconds: float = 0.2) -> bool:
        if not self.is_local_endpoint:
            return False
        try:
            parsed = urlparse(self.base_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds):
                return True
        except (OSError, TypeError, ValueError):
            return False


@dataclass
class AppConfig:
    target_language: str = "zh-CN"
    cache_db: Path = Path(".cache/translation.db")
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        return self.providers.get(provider_id)

    def first_available_provider(self) -> str:
        """
        Return a fully configured real provider, otherwise fallback to mock.
        """
        ready = [
            (provider_id, provider)
            for provider_id, provider in self.providers.items()
            if provider_id != "mock" and provider.is_ready
        ]
        for provider_id, provider in ready:
            if not provider.is_local_endpoint:
                return provider_id
        for provider_id, provider in ready:
            if provider.is_local_endpoint and provider.local_endpoint_reachable():
                return provider_id
        return "mock"


def _default_providers() -> dict[str, ProviderConfig]:
    return {
        "mock": ProviderConfig(
            id="mock",
            type="mock",
        ),
        "gemini": ProviderConfig(
            id="gemini",
            type="openai_compatible",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            model="gemini-3.5-flash-lite",
            api_key_env="GEMINI_API_KEY",
            timeout_seconds=60,
            max_retries=3,
            retry_backoff_seconds=1.5,
            temperature=0.2,
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
        "ollama": ProviderConfig(
            id="ollama",
            type="openai_compatible",
            base_url="http://127.0.0.1:11434/v1/chat/completions",
            model="qwen2.5:7b",
            timeout_seconds=180,
            max_retries=1,
            retry_backoff_seconds=1.0,
            temperature=0.2,
        ),
    }


def load_config(
    config_path: Path | str | None = None,
    env_path: Path | str | None = None,
) -> AppConfig:
    """
    Load config from environment and optional config.toml.

    Runtime behavior:

      - .env defaults to app_root()/.env
      - config.toml defaults to app_root()/config.toml
      - if config.toml does not exist but config.example.toml exists,
        use config.example.toml
      - relative cache_db paths are resolved against app_root()
    """
    root = app_root()

    if env_path is None:
        env_path = secrets_env_path()

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
    if getattr(sys, "frozen", False):
        # Read existing per-build fallback secrets during migration, without
        # copying them into a release or overriding the stable user secret.
        legacy_env = Path(sys.executable).resolve().parent / ".env"
        if legacy_env != Path(env_path):
            load_dotenv(legacy_env)

    target_language = "zh-CN"
    cache_db = root / ".cache" / "translation.db"

    config_file = Path(config_path)
    # A real configuration file is authoritative. Starting with every built-in
    # preset here would silently resurrect a provider after the user deleted it
    # in Settings. Defaults are only used when no configuration exists.
    providers = (
        _default_providers()
        if not config_file.exists()
        else {"mock": ProviderConfig(id="mock", type="mock")}
    )

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

    # Load configured provider secrets from the Windows credential vault.  The
    # legacy .env loader runs first so existing installations remain compatible.
    for provider_config in providers.values():
        if provider_config.api_key_env:
            get_secret(provider_config.api_key_env)

    return AppConfig(
        target_language=target_language,
        cache_db=cache_db,
        providers=providers,
    )
