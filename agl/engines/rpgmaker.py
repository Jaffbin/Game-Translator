from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from .base import EngineHandler
from ..models import EntryStatus, TranslationEntry, make_entry_id
from ..placeholders import PlaceholderProtector


_PLACEHOLDER_PROTECTOR = PlaceholderProtector()


# Safe database fields for RPG Maker MV/MZ.
# Intentionally conservative. Do not extract note fields by default,
# because they often contain plugin commands.
DATABASE_TEXT_FIELDS: Dict[str, List[str]] = {
    "Actors.json": ["name", "nickname", "profile"],
    "Classes.json": ["name"],
    "Skills.json": ["name", "description", "message1", "message2"],
    "Items.json": ["name", "description"],
    "Weapons.json": ["name", "description"],
    "Armors.json": ["name", "description"],
    "Enemies.json": ["name"],
    "States.json": [
        "name",
        "message1",
        "message2",
        "message3",
        "message4",
    ],
    "MapInfos.json": ["name"],
}


EVENT_FILE_NAMES = {
    "CommonEvents.json",
    "Troops.json",
}

MAP_FILE_REGEX = re.compile(r"^Map\d+\.json$")

# Show Text
EVENT_CODE_SHOW_TEXT = 401

# Show Choices
EVENT_CODE_SHOW_CHOICES = 102

FILE_EXTENSION_REGEX = re.compile(
    r"\.("
    r"png|jpe?g|gif|bmp|svg|"
    r"woff2?|ttf|otf|eot|"
    r"ogg|mp3|wav|m4a|"
    r"webm|mp4|"
    r"json|js|csv|xml|txt|"
    r"rpgmvp|rpgmvm|rpgmvo"
    r")$",
    re.IGNORECASE,
)


class RPGMakerHandler(EngineHandler):
    """
    RPG Maker MV/MZ handler.

    Safety rules:
      - Never use raw string replacement on JSON files.
      - Extract using JSON paths.
      - Inject using JSON paths.
      - Preserve control codes through pipeline placeholder protection.
    """

    engine_id = "rpgmaker_mv_mz"
    display_name = "RPG Maker MV/MZ"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, game_path: str) -> bool:
        return self._data_dir(game_path) is not None

    def _data_dir(self, game_path: str) -> Path | None:
        root = Path(game_path)

        candidates = [
            root / "www" / "data",
            root / "data",
        ]

        for candidate in candidates:
            if candidate.is_dir():
                return candidate

        return None

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def find_text_files(self, game_path: str) -> List[str]:
        data_dir = self._data_dir(game_path)
        if data_dir is None:
            return []

        files: List[str] = []

        for path in sorted(data_dir.glob("*.json")):
            name = path.name

            if (
                name in DATABASE_TEXT_FIELDS
                or name in EVENT_FILE_NAMES
                or MAP_FILE_REGEX.match(name)
            ):
                files.append(str(path))

        return files

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

        data = json.loads(path.read_text(encoding="utf-8"))

        entries: List[TranslationEntry] = []
        seen: Set[str] = set()

        name = path.name

        if name in DATABASE_TEXT_FIELDS:
            self._extract_database(
                data=data,
                fields=DATABASE_TEXT_FIELDS[name],
                rel_path=rel_path,
                entries=entries,
                seen=seen,
            )

        elif name in EVENT_FILE_NAMES or MAP_FILE_REGEX.match(name):
            self._walk_event_commands(
                obj=data,
                path=[],
                rel_path=rel_path,
                entries=entries,
                seen=seen,
            )

        return entries

    def _extract_database(
        self,
        data: Any,
        fields: List[str],
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        """
        Extract simple database fields:

          [
            null,
            {"name": "Potion", "description": "Restores HP"},
            ...
          ]
        """
        if not isinstance(data, list):
            return

        for index, record in enumerate(data):
            if not isinstance(record, dict):
                continue

            for field in fields:
                if field not in record:
                    continue

                value = record[field]
                if not isinstance(value, str):
                    continue

                json_path: List[Any] = [index, field]
                context = f"{rel_path} -> [{index}].{field}"

                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=value,
                    rel_path=rel_path,
                    json_path=json_path,
                    context=context,
                )

    def _walk_event_commands(
        self,
        obj: Any,
        path: List[Any],
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        """
        Recursively walk event data and extract:

          - code 401: Show Text
          - code 102: Show Choices
        """
        if isinstance(obj, dict):
            code = obj.get("code")
            parameters = obj.get("parameters")

            if (
                code == EVENT_CODE_SHOW_TEXT
                and isinstance(parameters, list)
                and len(parameters) > 0
            ):
                text = parameters[0]
                if isinstance(text, str):
                    json_path = path + ["parameters", 0]
                    context = f"{rel_path} -> Show Text"

                    self._add_entry(
                        entries=entries,
                        seen=seen,
                        source_text=text,
                        rel_path=rel_path,
                        json_path=json_path,
                        context=context,
                    )

            elif (
                code == EVENT_CODE_SHOW_CHOICES
                and isinstance(parameters, list)
                and len(parameters) > 0
                and isinstance(parameters[0], list)
            ):
                choices = parameters[0]

                for choice_index, choice in enumerate(choices):
                    if not isinstance(choice, str):
                        continue

                    json_path = path + ["parameters", 0, choice_index]
                    context = f"{rel_path} -> Show Choices"

                    self._add_entry(
                        entries=entries,
                        seen=seen,
                        source_text=choice,
                        rel_path=rel_path,
                        json_path=json_path,
                        context=context,
                    )

            for key, value in obj.items():
                self._walk_event_commands(
                    obj=value,
                    path=path + [key],
                    rel_path=rel_path,
                    entries=entries,
                    seen=seen,
                )

        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                self._walk_event_commands(
                    obj=value,
                    path=path + [index],
                    rel_path=rel_path,
                    entries=entries,
                    seen=seen,
                )

    def _add_entry(
        self,
        entries: List[TranslationEntry],
        seen: Set[str],
        source_text: str,
        rel_path: str,
        json_path: List[Any],
        context: str,
    ) -> None:
        if not self._is_translatable(source_text):
            return

        location = {
            "type": "json_path",
            "path": json_path,
        }

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

    def _is_translatable(self, text: Any) -> bool:
        if not isinstance(text, str):
            return False

        text = text.strip()

        if not text:
            return False

        # Skip obvious filenames/assets.
        if FILE_EXTENSION_REGEX.search(text):
            return False

        # Skip pure numbers.
        if re.fullmatch(r"[\d\s]+", text):
            return False

        # Skip strings that contain only control codes/placeholders.
        protected, _ = _PLACEHOLDER_PROTECTOR.protect(text)
        cleaned = _PLACEHOLDER_PROTECTOR.TOKEN_REGEX.sub("", protected).strip()

        if not cleaned:
            return False

        return True

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def inject_file(
        self,
        file_path: str,
        entries: List[TranslationEntry],
        output_path: str,
    ) -> int:
        """
        Safely inject translations by JSON path.

        Returns number of changed strings.
        """
        source_path = Path(file_path)
        target_path = Path(output_path)

        data = json.loads(source_path.read_text(encoding="utf-8"))

        changed = 0

        for entry in entries:
            if entry.ignored:
                continue

            if entry.status == EntryStatus.ERROR:
                continue

            if entry.target_text is None:
                continue

            json_path = entry.location.get("path") if entry.location else None
            if not json_path:
                continue

            try:
                self._set_by_path(data, json_path, entry.target_text)
                changed += 1
            except Exception as exc:
                print(
                    f"[WARN] Failed to inject entry {entry.id} "
                    f"at {json_path}: {exc}"
                )

        target_path.parent.mkdir(parents=True, exist_ok=True)

        with target_path.open("w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                separators=(",", ":"),
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