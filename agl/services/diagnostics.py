"""Read-only checks before a game is scanned or patched."""

from __future__ import annotations

import codecs
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from ..engines import detect_handler


FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
MAX_ISSUE_FILES = 20


def _encoding_of(path: Path) -> str:
    """Check the whole file, including chunks after the first megabyte."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    with path.open("rb") as stream:
        first = stream.read(4)
        if first.startswith(b"\xff\xfe") or first.startswith(b"\xfe\xff"):
            return "utf-16 (unsupported)"
        if first.startswith(b"\xef\xbb\xbf"):
            label = "utf-8-bom"
            first = first[3:]
        else:
            label = "utf-8"
        decoder.decode(first)
        while chunk := stream.read(1024 * 1024):
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    return label


def _font_files(root: Path, engine_id: str) -> list[Path]:
    folders = [root / "fonts", root / "www" / "fonts"]
    if engine_id == "renpy":
        folders.append(root / "game" / "fonts")
    if engine_id == "unity_lightweight":
        folders.extend([root / "StreamingAssets" / "fonts", root / "Assets" / "Resources" / "fonts"])
        folders.extend(root.glob("*_Data/StreamingAssets/fonts"))
    found: dict[str, Path] = {}
    for folder in folders:
        if folder.is_dir():
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in FONT_SUFFIXES:
                    found[str(path.resolve())] = path
    return sorted(found.values())


def _runtime_markers(root: Path, engine_id: str) -> list[str]:
    if engine_id == "rpgmaker_mv_mz":
        markers = []
        for folder in (root / "www" / "js", root / "js"):
            if (folder / "rmmz_core.js").is_file():
                markers.append("RPG Maker MZ")
            elif (folder / "rpg_core.js").is_file():
                markers.append("RPG Maker MV")
        return markers or ["RPG Maker MV/MZ data"]
    if engine_id == "renpy":
        return ["Ren'Py scripts"] if list((root / "game").glob("*.rpy")) else ["Ren'Py compiled scripts"]
    markers = []
    if (root / "GameAssembly.dll").is_file():
        markers.append("Unity IL2CPP")
    if (root / "UnityPlayer.dll").is_file():
        markers.append("Unity Player")
    return markers or ["Unity data directory"]


def diagnose_game(game_path: Path | str, target_language: str = "zh-CN") -> dict[str, Any]:
    root = Path(game_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Game folder does not exist: {root}")
    handler = detect_handler(str(root))
    if handler is None:
        raise RuntimeError("Unsupported game folder.")

    issues: list[dict[str, str]] = []
    files = handler.find_text_files(str(root))
    encoding_counts: dict[str, int] = {}
    for file_name in files:
        path = Path(file_name)
        try:
            label = _encoding_of(path)
        except UnicodeDecodeError:
            label = "invalid-utf-8"
        except OSError as exc:
            label = "unreadable"
            if len(issues) < MAX_ISSUE_FILES:
                issues.append({"severity": "error", "code": "file_unreadable", "message": f"{path.name}: {exc}"})
        encoding_counts[label] = encoding_counts.get(label, 0) + 1
        if label in {"invalid-utf-8", "utf-16 (unsupported)"} and len(issues) < MAX_ISSUE_FILES:
            issues.append({"severity": "error", "code": "encoding_unsupported", "message": f"{path.name}: {label}"})

    if not files:
        issues.append({"severity": "warning", "code": "no_text_files", "message": "没有找到可读取的文本文件。"})

    fonts = _font_files(root, handler.engine_id)
    font_names = [path.relative_to(root).as_posix() for path in fonts]
    font_status = "not_checked"
    if target_language.lower().startswith(("zh", "ja", "ko")):
        font_status = "unknown_coverage" if fonts else "no_bundled_font"
        issues.append({
            "severity": "warning",
            "code": "font_coverage_unverified",
            "message": (
                "找到游戏字体，但尚未验证目标文字的字形覆盖；请在游戏内检查显示。"
                if fonts else "未在常见字体目录找到字体；可能依赖系统字体，请在游戏内检查显示。"
            ),
        })

    free_bytes = shutil.disk_usage(root).free
    source_bytes = 0
    for name in files:
        try:
            source_bytes += Path(name).stat().st_size
        except OSError:
            # A file can disappear between discovery and this estimate. The
            # earlier encoding pass already records unreadable sources.
            pass
    estimated_needed = source_bytes * 3 + 50 * 1024 * 1024
    if free_bytes < estimated_needed:
        issues.append({"severity": "error", "code": "disk_space_low", "message": "游戏所在磁盘的剩余空间可能不足以生成备份和安装补丁。"})
    if not os.access(root, os.W_OK):
        issues.append({"severity": "warning", "code": "folder_not_writable", "message": "游戏文件夹可能没有写入权限。"})

    return {
        "engine": handler.engine_id,
        "runtime": {"host": platform.system(), "markers": _runtime_markers(root, handler.engine_id)},
        "encoding": {"files_checked": len(files), "counts": encoding_counts},
        "fonts": {"files": font_names[:30], "total": len(fonts), "coverage": font_status},
        "storage": {"free_bytes": free_bytes, "estimated_needed_bytes": estimated_needed},
        "issues": issues,
        "can_scan": not any(item["severity"] == "error" for item in issues),
    }
