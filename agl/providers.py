from __future__ import annotations
from dataclasses import replace

import time
from abc import ABC, abstractmethod
from typing import Dict, Optional

import requests

from .config import ProviderConfig


class TranslationProviderError(RuntimeError):
    pass


class TranslationProvider(ABC):
    id: str = "base"
    model: str = "base"

    @abstractmethod
    def translate(
        self,
        text: str,
        target_language: str,
        context: str = "",
        glossary: Optional[Dict[str, str]] = None,
    ) -> str:
        raise NotImplementedError


class MockProvider(TranslationProvider):
    """
    Deterministic provider for tests and offline development.
    """

    id = "mock"
    model = "mock"

    def translate(
        self,
        text: str,
        target_language: str,
        context: str = "",
        glossary: Optional[Dict[str, str]] = None,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        return f"[{target_language}] {text}"


class OpenAICompatibleProvider(TranslationProvider):
    """
    Works with:
      - NVIDIA NIM
      - OpenAI
      - DeepSeek
      - Moonshot
      - local OpenAI-compatible servers
    """

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.id = config.id
        self.model = config.model

    def _headers(self) -> Dict[str, str]:
        if not self.config.base_url:
            raise TranslationProviderError(
                f"Provider '{self.id}' has no base_url configured."
            )

        api_key = self.config.api_key
        if not api_key:
            raise TranslationProviderError(
                f"Missing API key for provider '{self.id}'. "
                f"Set environment variable: {self.config.api_key_env}"
            )

        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def _system_prompt(self, target_language: str) -> str:
        return (
            "You are a professional game localizer.\n"
            f"Translate game text into {target_language}.\n"
            "Rules:\n"
            "1. Output only the translated text.\n"
            "2. Do not add explanations.\n"
            "3. Preserve placeholders such as [[AGL_PH_0]] exactly.\n"
            "4. Keep glossary terms consistent if a glossary is provided.\n"
            "5. Preserve game tone and style.\n"
        )

    def _user_prompt(
        self,
        text: str,
        target_language: str,
        context: str = "",
        glossary: Optional[Dict[str, str]] = None,
    ) -> str:
        parts = []

        if context:
            parts.append(f"Context: {context}")

        if glossary:
            glossary_lines = []
            for source_term, target_term in glossary.items():
                glossary_lines.append(f"{source_term} -> {target_term}")
            parts.append("Glossary:")
            parts.extend(glossary_lines)

        parts.append("Text:")
        parts.append(text)
        parts.append("")
        parts.append(f"Translate to {target_language}.")

        return "\n".join(parts)

    def translate(
        self,
        text: str,
        target_language: str,
        context: str = "",
        glossary: Optional[Dict[str, str]] = None,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": 2048,
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(target_language),
                },
                {
                    "role": "user",
                    "content": self._user_prompt(
                        text=text,
                        target_language=target_language,
                        context=context,
                        glossary=glossary,
                    ),
                },
            ],
        }

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                response = requests.post(
                    self.config.base_url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )

                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    raise TranslationProviderError(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )

                response.raise_for_status()

                data = response.json()

                return data["choices"][0]["message"]["content"].strip()

            except TranslationProviderError as exc:
                last_error = exc

            except requests.RequestException as exc:
                last_error = exc

            except (KeyError, IndexError, ValueError) as exc:
                last_error = TranslationProviderError(
                    f"Invalid translation API response: {exc}"
                )

            if attempt < self.config.max_retries:
                sleep_seconds = self.config.retry_backoff_seconds * (2 ** attempt)
                time.sleep(sleep_seconds)

        raise TranslationProviderError(
            f"Translation failed for provider '{self.id}': {last_error}"
        )


def create_provider(
    provider_config: ProviderConfig,
    model_override: Optional[str] = None,
) -> TranslationProvider:
    """
    Create translation provider.

    model_override allows runtime model switching:
      --provider nvidia --model meta/llama-3.1-70b-instruct
    """
    cfg = provider_config

    if model_override:
        cfg = replace(cfg, model=model_override)

    if cfg.type == "mock":
        return MockProvider()

    if cfg.type == "openai_compatible":
        return OpenAICompatibleProvider(cfg)

    raise TranslationProviderError(
        f"Unknown provider type: {cfg.type}"
    )