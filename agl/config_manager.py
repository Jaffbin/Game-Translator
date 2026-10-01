from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

from .config import ProviderConfig, load_dotenv
from .providers import create_provider
from .workspace import app_root, secrets_env_path
from .secrets import get_secret, set_secret
from .io_utils import atomic_write_text


DEFAULT_CONFIG_DATA: Dict[str, Any] = {
    "translation": {
        "target_language": "zh-CN",
    },
    "project": {
        "cache_db": ".cache/translation.db",
    },
    "providers": {
        "mock": {
            "type": "mock",
        },
        "gemini": {
            "type": "openai_compatible",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "model": "gemini-3.5-flash-lite",
            "api_key_env": "GEMINI_API_KEY",
            "timeout_seconds": 60,
            "max_retries": 3,
            "retry_backoff_seconds": 1.5,
            "temperature": 0.2,
        },
        "nvidia": {
            "type": "openai_compatible",
            "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
            "model": "meta/llama-3.1-8b-instruct",
            "api_key_env": "NVIDIA_API_KEY",
            "timeout_seconds": 60,
            "max_retries": 3,
            "retry_backoff_seconds": 1.5,
            "temperature": 0.2,
        },
        "ollama": {
            "type": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1/chat/completions",
            "model": "qwen2.5:7b",
            "api_key_env": "",
            "timeout_seconds": 180,
            "max_retries": 1,
            "retry_backoff_seconds": 1.0,
            "temperature": 0.2,
        },
    },
}


PROVIDER_ID_REGEX = re.compile(r"^[A-Za-z0-9_\-]+$")
ENV_KEY_REGEX = re.compile(r"^[A-Z][A-Z0-9_]*$")


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------

def config_path() -> Path:
    return app_root() / "config.toml"


def env_path() -> Path:
    return secrets_env_path()


def _load_env_fallbacks() -> None:
    load_dotenv(env_path())
    if getattr(sys, "frozen", False):
        legacy = Path(sys.executable).resolve().parent / ".env"
        if legacy != env_path():
            load_dotenv(legacy)


def effective_config_path() -> Optional[Path]:
    real = config_path()

    if real.exists():
        return real

    example = app_root() / "config.example.toml"

    if example.exists():
        return example

    return None


# ----------------------------------------------------------------------
# TOML helpers
# ----------------------------------------------------------------------

def _toml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_\-]+", key):
        return key

    return _toml_value(str(key))


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        return repr(value)

    if isinstance(value, str):
        return _toml_str(value)

    # Fallback
    return _toml_str(str(value))


def _render_toml(data: Dict[str, Any]) -> str:
    lines: List[str] = []

    for section_name in ("translation", "project"):
        section = data.get(section_name)

        if not isinstance(section, dict):
            continue

        lines.append(f"[{section_name}]")

        for key, value in section.items():
            if value is None:
                continue

            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

        lines.append("")

    providers = data.get("providers", {})

    for provider_id, provider_data in providers.items():
        if not isinstance(provider_data, dict):
            continue

        lines.append(f"[providers.{_toml_key(provider_id)}]")

        for key, value in provider_data.items():
            if value is None:
                continue

            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

        lines.append("")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# Config read / save
# ----------------------------------------------------------------------

def read_config_data() -> Dict[str, Any]:
    path = effective_config_path()

    if path is None:
        return copy.deepcopy(DEFAULT_CONFIG_DATA)

    if tomllib is None:
        print(
            "[WARN] tomllib/tomli unavailable. "
            "Using default config data."
        )
        return copy.deepcopy(DEFAULT_CONFIG_DATA)

    text = path.read_text(encoding="utf-8")

    try:
        data = tomllib.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config file {path}: {exc}")

    return data


def save_config_data(data: Dict[str, Any]) -> Path:
    text = _render_toml(data)
    path = config_path()

    atomic_write_text(path, text)

    return path


# ----------------------------------------------------------------------
# Public settings, no secrets
# ----------------------------------------------------------------------

def get_public_settings() -> Dict[str, Any]:
    _load_env_fallbacks()
    data = read_config_data()

    providers_public: Dict[str, Any] = {}

    for provider_id, provider_data in data.get("providers", {}).items():
        if not isinstance(provider_data, dict):
            continue

        api_key_env = provider_data.get("api_key_env", "")

        has_api_key = bool(api_key_env and get_secret(api_key_env))
        base_url = str(provider_data.get("base_url", ""))
        try:
            is_local = urlparse(base_url).hostname in {"127.0.0.1", "localhost", "::1"}
        except ValueError:
            is_local = False
        local_available = False
        if is_local:
            local_available = ProviderConfig(
                id=provider_id,
                type=str(provider_data.get("type", "openai_compatible")),
                base_url=base_url,
                model=str(provider_data.get("model", "")),
            ).local_endpoint_reachable()

        providers_public[provider_id] = {
            **provider_data,
            "has_api_key": has_api_key,
            "is_local": is_local,
            "is_available": local_available if is_local else has_api_key,
            "requires_api_key": provider_data.get("type") != "mock" and not is_local,
        }

    return {
        "translation": data.get("translation", {}),
        "project": data.get("project", {}),
        "providers": providers_public,
    }


def save_general_settings(
    target_language: Optional[str] = None,
    cache_db: Optional[str] = None,
) -> Dict[str, Any]:
    data = read_config_data()

    if target_language:
        data.setdefault("translation", {})["target_language"] = (
            target_language.strip()
        )

    if cache_db:
        data.setdefault("project", {})["cache_db"] = cache_db.strip()

    save_config_data(data)

    return data


# ----------------------------------------------------------------------
# Provider CRUD
# ----------------------------------------------------------------------

def upsert_provider(provider: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = str(provider.get("id", "")).strip()

    if not provider_id:
        raise ValueError("Provider id is required.")

    if not PROVIDER_ID_REGEX.match(provider_id):
        raise ValueError(
            "Provider id may only contain letters, numbers, '_' and '-'."
        )

    provider_type = str(provider.get("type", "openai_compatible")).strip()

    if provider_type not in {"mock", "openai_compatible"}:
        raise ValueError(
            "Provider type must be 'mock' or 'openai_compatible'."
        )

    entry: Dict[str, Any] = {
        "type": provider_type,
    }

    if provider_type == "mock":
        # Mock provider does not need extra settings.
        pass
    else:
        base_url = str(provider.get("base_url", "")).strip()
        model = str(provider.get("model", "")).strip()
        api_key_env = str(provider.get("api_key_env", "")).strip()

        if not base_url:
            raise ValueError("base_url is required for openai_compatible provider.")

        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("base_url must be a valid http:// or https:// URL.")

        if not model:
            raise ValueError("model is required for openai_compatible provider.")

        try:
            timeout_seconds = int(provider.get("timeout_seconds", 60))
        except Exception:
            timeout_seconds = 60

        try:
            max_retries = int(provider.get("max_retries", 3))
        except Exception:
            max_retries = 3

        try:
            retry_backoff_seconds = float(
                provider.get("retry_backoff_seconds", 1.5)
            )
        except Exception:
            retry_backoff_seconds = 1.5

        try:
            temperature = float(provider.get("temperature", 0.2))
        except Exception:
            temperature = 0.2

        if not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600.")
        if not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10.")
        if not 0 <= retry_backoff_seconds <= 60:
            raise ValueError("retry_backoff_seconds must be between 0 and 60.")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2.")

        entry.update(
            {
                "base_url": base_url,
                "model": model,
                "api_key_env": api_key_env,
                "timeout_seconds": timeout_seconds,
                "max_retries": max_retries,
                "retry_backoff_seconds": retry_backoff_seconds,
                "temperature": temperature,
            }
        )

    data = read_config_data()
    data.setdefault("providers", {})[provider_id] = entry

    save_config_data(data)

    return entry


def delete_provider(provider_id: str) -> None:
    provider_id = provider_id.strip()

    if provider_id == "mock":
        raise ValueError("Built-in mock provider cannot be deleted.")

    data = read_config_data()

    providers = data.get("providers", {})

    if provider_id not in providers:
        raise ValueError(f"Provider not found: {provider_id}")

    del providers[provider_id]

    save_config_data(data)


# ----------------------------------------------------------------------
# .env helpers
# ----------------------------------------------------------------------

def _env_quote(value: str) -> str:
    value = str(value)

    if value == "":
        return ""

    if re.search(r"\s|#|\"", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return value


def list_env_keys() -> List[Dict[str, Any]]:
    _load_env_fallbacks()
    data = read_config_data()

    keys: List[str] = []

    for provider_data in data.get("providers", {}).values():
        if not isinstance(provider_data, dict):
            continue

        api_key_env = provider_data.get("api_key_env", "")

        if api_key_env and api_key_env not in keys:
            keys.append(api_key_env)

    items: List[Dict[str, Any]] = []

    for key in keys:
        items.append(
            {
                "key": key,
                "has_value": bool(get_secret(key)),
            }
        )

    return items


def save_env_variable(key: str, value: str) -> None:
    key = key.strip()

    if not ENV_KEY_REGEX.match(key):
        raise ValueError(
            "Environment variable name must match ^[A-Z][A-Z0-9_]*$"
        )

    # Prefer the OS credential vault.  Fall back to the legacy .env file only
    # when keyring is unavailable, preserving source-checkout compatibility.
    if set_secret(key, value):
        return

    path = env_path()

    lines: List[str] = []

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    new_line = f"{key}={_env_quote(value)}"

    replaced = False

    for index, line in enumerate(lines):
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            lines[index] = new_line
            replaced = True
            break

    if not replaced:
        lines.append(new_line)

    atomic_write_text(path, "\n".join(lines) + "\n")


# ----------------------------------------------------------------------
# Provider test
# ----------------------------------------------------------------------

def test_provider(provider_id: str) -> Dict[str, Any]:
    provider_id = provider_id.strip()
    # The Settings test can run before load_config(), so load the documented
    # local fallback when Windows Credential Manager is unavailable.
    _load_env_fallbacks()

    data = read_config_data()

    provider_data = data.get("providers", {}).get(provider_id)

    if not isinstance(provider_data, dict):
        raise ValueError(f"Provider not found: {provider_id}")

    target_language = (
        data.get("translation", {}).get("target_language", "zh-CN")
    )

    try:
        timeout_seconds = int(provider_data.get("timeout_seconds", 30))
    except Exception:
        timeout_seconds = 30

    try:
        max_retries = int(provider_data.get("max_retries", 1))
    except Exception:
        max_retries = 1

    try:
        retry_backoff_seconds = float(
            provider_data.get("retry_backoff_seconds", 1.0)
        )
    except Exception:
        retry_backoff_seconds = 1.0

    try:
        temperature = float(provider_data.get("temperature", 0.2))
    except Exception:
        temperature = 0.2

    provider_config = ProviderConfig(
        id=provider_id,
        type=provider_data.get("type", "openai_compatible"),
        base_url=provider_data.get("base_url", ""),
        model=provider_data.get("model", ""),
        api_key_env=provider_data.get("api_key_env", ""),
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        temperature=temperature,
    )

    provider = create_provider(provider_config)

    source_text = "Hello."

    translated = provider.translate(
        text=source_text,
        target_language=target_language,
        context="Settings test",
    )

    return {
        "provider_id": provider_id,
        "model": provider.model,
        "source_text": source_text,
        "target_language": target_language,
        "translated_text": translated[:200],
    }
