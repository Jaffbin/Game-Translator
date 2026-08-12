from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..models import TranslationEntry


class EngineHandler(ABC):
    """
    Base class for game engine handlers.

    Each engine handler must support:
      - detection
      - file discovery
      - extraction into stable TranslationEntry objects
      - safe injection into output files
    """

    engine_id: str = "base"
    display_name: str = "Base Engine"

    @abstractmethod
    def detect(self, game_path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def find_text_files(self, game_path: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def extract_file(
        self,
        file_path: str,
        game_root: str,
    ) -> List[TranslationEntry]:
        raise NotImplementedError

    @abstractmethod
    def inject_file(
        self,
        file_path: str,
        entries: List[TranslationEntry],
        output_path: str,
    ) -> int:
        raise NotImplementedError