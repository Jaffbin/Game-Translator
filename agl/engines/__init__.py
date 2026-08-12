from .base import EngineHandler
from .rpgmaker import RPGMakerHandler
from .renpy import RenPyHandler
from .unity import UnityHandler


def get_handlers():
    """
    Engine registry.

    Priority:
      1. RPG Maker MV/MZ
      2. Ren'Py
      3. Unity lightweight text
    """
    return [
        RPGMakerHandler(),
        RenPyHandler(),
        UnityHandler(),
    ]


def detect_handler(game_path: str) -> EngineHandler | None:
    for handler in get_handlers():
        if handler.detect(game_path):
            return handler
    return None


def get_handler_by_id(engine_id: str) -> EngineHandler | None:
    for handler in get_handlers():
        if handler.engine_id == engine_id:
            return handler
    return None


__all__ = [
    "EngineHandler",
    "RPGMakerHandler",
    "RenPyHandler",
    "UnityHandler",
    "get_handlers",
    "detect_handler",
    "get_handler_by_id",
]