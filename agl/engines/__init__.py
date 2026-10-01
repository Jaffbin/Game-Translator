from pathlib import Path

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


def resolve_game_folder(selected_path: str | Path) -> tuple[Path, EngineHandler]:
    """Locate one supported game near the folder selected in Explorer.

    The picker is often used to select a container folder or an engine's data
    folder. Search only a shallow, bounded area and never guess between games.
    """
    selected = Path(selected_path).expanduser().resolve()
    if not selected.is_dir():
        raise FileNotFoundError(f"游戏文件夹不存在：{selected}")

    handler = detect_handler(str(selected))
    if handler is not None:
        return selected, handler

    if selected.name.lower() in {"data", "www", "game", "streamingassets"}:
        parent = selected.parent
        handler = detect_handler(str(parent))
        if handler is not None:
            return parent, handler
        if selected.name.lower() == "data" and parent.name.lower() == "www":
            handler = detect_handler(str(parent.parent))
            if handler is not None:
                return parent.parent, handler

    def children(folder: Path) -> list[Path]:
        try:
            entries = sorted(
                (path for path in folder.iterdir() if path.is_dir() and not path.is_symlink()),
                key=lambda path: path.name.casefold(),
            )
        except OSError:
            return []
        if len(entries) > 100:
            raise ValueError("这个文件夹包含太多子目录，请选择更具体的游戏文件夹。")
        return entries

    level = children(selected)
    for depth in (1, 2):
        candidates = level if depth == 1 else [grandchild for child in level for grandchild in children(child)]
        if len(candidates) > 100:
            raise ValueError("这个文件夹范围过大，请选择更具体的游戏文件夹。")
        matches = [(path, found) for path in candidates if (found := detect_handler(str(path))) is not None]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = "、".join(str(path.relative_to(selected)) for path, _ in matches[:3])
            raise ValueError(f"找到多个游戏（{names}）。请选择其中一个游戏的文件夹。")

    raise ValueError(
        "未找到支持的游戏。请选择包含 data 或 www/data（RPG Maker）、"
        "game（Ren'Py）或 *_Data / StreamingAssets（Unity）的游戏文件夹。"
    )


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
    "resolve_game_folder",
    "get_handler_by_id",
]
