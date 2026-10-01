from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import EngineHandler
from ..models import EntryStatus, TranslationEntry, make_entry_id
from ..placeholders import PlaceholderProtector


_PLACEHOLDER_PROTECTOR = PlaceholderProtector()


# Conservative limits for MVP.
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_FILES = 1000

TEXT_EXTENSIONS = {
    ".json",
    ".csv",
    ".txt",
}


# Keys that are very likely translatable in Unity JSON files.
TRANSLATABLE_KEYS = {
    "text",
    "title",
    "description",
    "desc",
    "content",
    "message",
    "msg",
    "dialog",
    "dialogue",
    "caption",
    "body",
    "name",
    "label",
    "hint",
    "tooltip",
    "summary",
}


# Keys that should usually be skipped.
SKIP_JSON_KEYS = {
    "id",
    "guid",
    "uuid",
    "hash",
    "key",
    "path",
    "file",
    "filename",
    "filepath",
    "dir",
    "folder",
    "icon",
    "sprite",
    "texture",
    "prefab",
    "scene",
    "asset",
    "assetbundle",
    "bundle",
    "audio",
    "sound",
    "music",
    "voice",
    "sfx",
    "bgm",
    "se",
    "font",
    "material",
    "shader",
    "color",
    "colour",
    "time",
    "timestamp",
    "version",
    "type",
    "tag",
    "layer",
    "mask",
    "index",
}


FILE_EXTENSION_REGEX = re.compile(
    r"\.("
    r"png|jpe?g|gif|bmp|svg|"
    r"woff2?|ttf|otf|eot|"
    r"ogg|mp3|wav|m4a|"
    r"webm|mp4|"
    r"json|js|csv|xml|txt|"
    r"asset|assets|unity3d|bundle|"
    r"prefab|mat|anim|controller|"
    r"dll|exe|so|apk"
    r")$",
    re.IGNORECASE,
)


URL_REGEX = re.compile(
    r"^(https?|ftp)://",
    re.IGNORECASE,
)


TEXT_KEY_VALUE_RE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z_][\w.-]*\s*[=:]\s*)"
    r"(?P<quote>[\"']?)(?P<value>.*?)(?P=quote)(?P<trailing>\s*)$"
)


CSV_HEADER_NAMES = {
    "id",
    "key",
    "name",
    "text",
    "title",
    "description",
    "dialog",
    "dialogue",
    "message",
    "source",
    "target",
    "translation",
    "language",
    "locale",
}


CJK_REGEX = re.compile(
    r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]"
)


def _read_text_preserve_bom(path: Path) -> Tuple[str, bool]:
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig"), has_bom


def _write_text_preserve_bom(
    path: Path,
    text: str,
    has_bom: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    # ``text`` may already contain CRLF from the source or CSV writer. Byte
    # output prevents Windows from expanding each LF a second time.
    path.write_bytes(encoded)


def _to_single_line(text: str) -> str:
    """
    TXT and CSV MVP injection keeps one-line cells/lines.
    Multi-line translations are flattened.
    """
    return (
        text.replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )


class UnityHandler(EngineHandler):
    """
    Unity lightweight text handler.

    This handler only supports external readable text files:

      - StreamingAssets/**/*.json
      - StreamingAssets/**/*.csv
      - StreamingAssets/**/*.txt
      - Resources/**/*.json
      - Resources/**/*.csv
      - *_Data/StreamingAssets/**/*.json

    It does NOT parse:
      - resources.assets
      - globalgamemanagers
      - AssetBundle
      - IL2CPP metadata
      - Unity serialized binary files
    """

    engine_id = "unity_lightweight"
    display_name = "Unity Lightweight Text"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, game_path: str) -> bool:
        root = Path(game_path)

        if (root / "GameAssembly.dll").exists():
            return True

        if (root / "UnityPlayer.dll").exists():
            return True

        if any(path.is_dir() for path in root.glob("*_Data")):
            return True

        if (root / "StreamingAssets").is_dir():
            return True

        return False

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def find_text_files(self, game_path: str) -> List[str]:
        root = Path(game_path)

        search_dirs: List[Path] = []

        # Common direct folders.
        for folder_name in ("StreamingAssets", "Resources", "Data"):
            folder = root / folder_name
            if folder.is_dir():
                search_dirs.append(folder)

        # Unity editor project style.
        assets_dir = root / "Assets"
        if assets_dir.is_dir():
            for folder_name in ("StreamingAssets", "Resources"):
                folder = assets_dir / folder_name
                if folder.is_dir():
                    search_dirs.append(folder)

        # Built Unity game style:
        #   GameName_Data/StreamingAssets
        #   GameName_Data/Resources
        for data_dir in root.glob("*_Data"):
            if not data_dir.is_dir():
                continue

            for folder_name in ("StreamingAssets", "Resources"):
                folder = data_dir / folder_name
                if folder.is_dir():
                    search_dirs.append(folder)

        files: List[str] = []
        seen_paths: Set[str] = set()

        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue

            for path in search_dir.rglob("*"):
                if not path.is_file():
                    continue

                if path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue

                try:
                    if path.stat().st_size > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue

                resolved = str(path.resolve())

                if resolved in seen_paths:
                    continue

                seen_paths.add(resolved)
                files.append(str(path))

                if len(files) >= MAX_FILES:
                    return sorted(files)

        return sorted(files)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_file(
        self,
        file_path: str,
        game_root: str,
    ) -> List[TranslationEntry]:
        path = Path(file_path)
        root = Path(game_root)

        try:
            rel_path = path.relative_to(root).as_posix()
        except ValueError:
            rel_path = path.name

        suffix = path.suffix.lower()

        entries: List[TranslationEntry] = []
        seen: Set[str] = set()

        try:
            if suffix == ".json":
                self._extract_json(path, rel_path, entries, seen)

            elif suffix == ".txt":
                self._extract_txt(path, rel_path, entries, seen)

            elif suffix == ".csv":
                self._extract_csv(path, rel_path, entries, seen)

        except Exception as exc:
            print(f"[WARN] Unity extraction failed for {rel_path}: {exc}")
            raise

        return entries

    def _extract_json(
        self,
        path: Path,
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        text, _ = _read_text_preserve_bom(path)
        data = json.loads(text)

        self._walk_json(
            obj=data,
            path=[],
            key_hint="",
            rel_path=rel_path,
            entries=entries,
            seen=seen,
        )

    def _walk_json(
        self,
        obj: Any,
        path: List[Any],
        key_hint: str,
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.lower() in SKIP_JSON_KEYS:
                    continue

                self._walk_json(
                    obj=value,
                    path=path + [key],
                    key_hint=key,
                    rel_path=rel_path,
                    entries=entries,
                    seen=seen,
                )

        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                self._walk_json(
                    obj=value,
                    path=path + [index],
                    key_hint=key_hint,
                    rel_path=rel_path,
                    entries=entries,
                    seen=seen,
                )

        elif isinstance(obj, str):
            if not self._is_translatable_value(obj, key_hint):
                return

            location = {
                "type": "unity_json_path",
                "path": path,
            }

            context = f"{rel_path} -> {'/'.join(str(p) for p in path)}"

            self._add_entry(
                entries=entries,
                seen=seen,
                source_text=obj,
                rel_path=rel_path,
                location=location,
                context=context,
            )

    def _extract_txt(
        self,
        path: Path,
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        text, _ = _read_text_preserve_bom(path)

        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()

            if not stripped:
                continue

            if stripped.startswith(("#", "//", ";", "[")):
                continue

            key_value = TEXT_KEY_VALUE_RE.match(line)
            if key_value:
                value = key_value.group("value").strip()
                if not self._is_translatable_value(value, "text"):
                    continue

                key = key_value.group("prefix").split("=", 1)[0].split(":", 1)[0].strip()
                location = {
                    "type": "unity_text_key_value",
                    "line": line_number,
                }
                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=value,
                    rel_path=rel_path,
                    location=location,
                    context=f"{rel_path}:{line_number} [{key}]",
                )
                continue

            if not self._is_translatable_value(stripped, "text"):
                continue

            location = {
                "type": "unity_text_line",
                "line": line_number,
            }

            context = f"{rel_path}:{line_number}"

            self._add_entry(
                entries=entries,
                seen=seen,
                source_text=stripped,
                rel_path=rel_path,
                location=location,
                context=context,
            )

    def _extract_csv(
        self,
        path: Path,
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        text, _ = _read_text_preserve_bom(path)

        if not text.strip():
            return

        try:
            dialect = csv.Sniffer().sniff(
                text[:4096],
                delimiters=",;\t|",
            )
        except csv.Error:
            dialect = csv.excel

        rows = list(
            csv.reader(
                io.StringIO(text),
                dialect,
            )
        )

        has_header = bool(rows and self._looks_like_csv_header(rows[0]))

        for row_index, row in enumerate(rows):
            if row_index == 0 and has_header:
                continue

            for col_index, cell in enumerate(row):
                if not self._is_translatable_value(cell, "text"):
                    continue

                location = {
                    "type": "unity_csv_cell",
                    "row": row_index,
                    "col": col_index,
                }

                context = f"{rel_path}:{row_index},{col_index}"

                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=cell.strip(),
                    rel_path=rel_path,
                    location=location,
                    context=context,
                )

    def _add_entry(
        self,
        entries: List[TranslationEntry],
        seen: Set[str],
        source_text: str,
        rel_path: str,
        location: Dict[str, Any],
        context: str,
    ) -> None:
        entry_id = make_entry_id(
            engine=self.engine_id,
            file_path=rel_path,
            location=location,
            source_text=source_text,
        )

        if entry_id in seen:
            return

        seen.add(entry_id)

        entries.append(
            TranslationEntry(
                id=entry_id,
                source_text=source_text,
                target_text=None,
                context=context,
                file_path=rel_path,
                engine=self.engine_id,
                location=location,
                status=EntryStatus.PENDING,
            )
        )

    def _is_translatable_value(self, text: Any, key_hint: str) -> bool:
        if not isinstance(text, str):
            return False

        text = text.strip()

        if not text:
            return False

        if FILE_EXTENSION_REGEX.search(text):
            return False

        if URL_REGEX.match(text):
            return False

        # Pure numbers / punctuation.
        if re.fullmatch(r"[\d\s\.\,\-_/:\\]+", text):
            return False

        # Skip strings that only contain control codes/placeholders.
        protected, _ = _PLACEHOLDER_PROTECTOR.protect(text)
        cleaned = _PLACEHOLDER_PROTECTOR.TOKEN_REGEX.sub("", protected).strip()

        if not cleaned:
            return False

        key_hint_lower = (key_hint or "").lower().strip()

        # Strong translatable key hint.
        if key_hint_lower in TRANSLATABLE_KEYS:
            return True

        # Common partial key hints.
        for hint_word in (
            "text",
            "title",
            "description",
            "message",
            "dialog",
            "caption",
            "label",
            "name",
        ):
            if hint_word in key_hint_lower:
                return True

        # CJK text is likely translatable if source is already CJK?
        # For translation to Chinese this may still be useful for cleanup,
        # but usually source is English. Keep it conservative.
        if CJK_REGEX.search(text):
            return True

        # Sentence-like English text.
        if re.search(r"[A-Za-z]", text) and re.search(r"\s", text):
            return True

        return False

    def _looks_like_csv_header(self, row: List[str]) -> bool:
        """Recognize common localization headers without dropping row 1 blindly."""
        normalized = [cell.strip().lower() for cell in row if cell.strip()]
        if not normalized:
            return False
        known = sum(
            cell in CSV_HEADER_NAMES
            or cell.endswith("_text")
            or cell.endswith("_id")
            for cell in normalized
        )
        identifier_like = sum(
            bool(re.fullmatch(r"[a-z_][a-z0-9_.-]*", cell))
            for cell in normalized
        )
        return known >= 1 and identifier_like == len(normalized)

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def inject_file(
        self,
        file_path: str,
        entries: List[TranslationEntry],
        output_path: str,
    ) -> int:
        source_path = Path(file_path)
        target_path = Path(output_path)

        suffix = source_path.suffix.lower()

        if suffix == ".json":
            return self._inject_json(source_path, entries, target_path)

        if suffix == ".txt":
            return self._inject_txt(source_path, entries, target_path)

        if suffix == ".csv":
            return self._inject_csv(source_path, entries, target_path)

        return 0

    def _inject_json(
        self,
        source_path: Path,
        entries: List[TranslationEntry],
        target_path: Path,
    ) -> int:
        text, has_bom = _read_text_preserve_bom(source_path)
        data = json.loads(text)

        changed = 0

        for entry in entries:
            if entry.ignored:
                continue

            if entry.status == EntryStatus.ERROR:
                continue

            if entry.target_text is None:
                continue

            if not entry.target_text.strip():
                continue

            location = entry.location or {}

            if location.get("type") != "unity_json_path":
                continue

            path = location.get("path")

            if not path:
                continue

            try:
                self._set_by_path(data, path, entry.target_text)
                changed += 1
            except Exception as exc:
                print(
                    f"[WARN] Unity JSON inject failed at {path}: {exc}"
                )

        target_path.parent.mkdir(parents=True, exist_ok=True)

        _write_text_preserve_bom(
            target_path,
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            has_bom,
        )

        return changed

    def _inject_txt(
        self,
        source_path: Path,
        entries: List[TranslationEntry],
        target_path: Path,
    ) -> int:
        text, has_bom = _read_text_preserve_bom(source_path)

        lines = text.splitlines(keepends=True)
        changed = 0

        for entry in entries:
            if entry.ignored:
                continue

            if entry.status == EntryStatus.ERROR:
                continue

            if entry.target_text is None:
                continue

            if not entry.target_text.strip():
                continue

            location = entry.location or {}

            location_type = location.get("type")
            if location_type not in {"unity_text_line", "unity_text_key_value"}:
                continue

            line_number = location.get("line")

            if not isinstance(line_number, int):
                continue

            if line_number < 1 or line_number > len(lines):
                continue

            original_line = lines[line_number - 1]

            ending = ""
            content = original_line

            if content.endswith("\n"):
                ending = "\n"
                content = content[:-1]

            if content.endswith("\r"):
                ending = "\r" + ending
                content = content[:-1]

            translated = _to_single_line(entry.target_text)
            if location_type == "unity_text_key_value":
                key_value = TEXT_KEY_VALUE_RE.match(content)
                if not key_value:
                    continue
                new_content = (
                    key_value.group("prefix")
                    + key_value.group("quote")
                    + translated
                    + key_value.group("quote")
                    + key_value.group("trailing")
                )
            else:
                # Keep formatting that may be meaningful to a line-oriented
                # game format (indentation and deliberate trailing spaces).
                leading = content[: len(content) - len(content.lstrip())]
                trailing = content[len(content.rstrip()) :]
                new_content = leading + translated + trailing

            lines[line_number - 1] = new_content + ending
            changed += 1

        target_path.parent.mkdir(parents=True, exist_ok=True)

        _write_text_preserve_bom(
            target_path,
            "".join(lines),
            has_bom,
        )

        return changed

    def _inject_csv(
        self,
        source_path: Path,
        entries: List[TranslationEntry],
        target_path: Path,
    ) -> int:
        text, has_bom = _read_text_preserve_bom(source_path)

        if not text.strip():
            return 0

        try:
            dialect = csv.Sniffer().sniff(
                text[:4096],
                delimiters=",;\t|",
            )
        except csv.Error:
            dialect = csv.excel

        rows = list(
            csv.reader(
                io.StringIO(text),
                dialect,
            )
        )

        changed = 0

        for entry in entries:
            if entry.ignored:
                continue

            if entry.status == EntryStatus.ERROR:
                continue

            if entry.target_text is None:
                continue

            if not entry.target_text.strip():
                continue

            location = entry.location or {}

            if location.get("type") != "unity_csv_cell":
                continue

            row_index = location.get("row")
            col_index = location.get("col")

            if not isinstance(row_index, int):
                continue

            if not isinstance(col_index, int):
                continue

            if row_index < 0 or row_index >= len(rows):
                continue

            row = rows[row_index]

            if col_index < 0 or col_index >= len(row):
                continue

            original_cell = row[col_index]
            leading = original_cell[: len(original_cell) - len(original_cell.lstrip())]
            trailing = original_cell[len(original_cell.rstrip()) :]
            row[col_index] = leading + _to_single_line(entry.target_text) + trailing
            changed += 1

        output = io.StringIO()

        writer = csv.writer(
            output,
            dialect,
        )

        writer.writerows(rows)

        target_path.parent.mkdir(parents=True, exist_ok=True)

        _write_text_preserve_bom(
            target_path,
            output.getvalue(),
            has_bom,
        )

        return changed

    def _set_by_path(
        self,
        root: Any,
        path: List[Any],
        value: str,
    ) -> None:
        if not path:
            return

        node = root

        for key in path[:-1]:
            node = node[key]

        node[path[-1]] = value
