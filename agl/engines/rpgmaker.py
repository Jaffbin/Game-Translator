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

# User-visible RPG Maker MV/MZ labels stored outside the normal database
# record arrays. Developer-only switch/variable labels are intentionally not
# included because changing them can make event maintenance and diagnostics
# unnecessarily confusing.
SYSTEM_SCALAR_TEXT_FIELDS = {
    "gameTitle",
    "currencyUnit",
}

SYSTEM_ARRAY_TEXT_FIELDS = {
    "armorTypes",
    "elements",
    "equipTypes",
    "skillTypes",
    "weaponTypes",
}

SYSTEM_TERM_ARRAY_FIELDS = {
    "basic",
    "commands",
    "params",
}

# Plugin parameters are an extension surface, so only explicitly known
# player-facing fields are extracted. Wildcards address array items inside
# RPG Maker's recursively JSON-encoded parameter strings.
PLUGIN_TEXT_FIELDS: Dict[str, List[List[str]]] = {
    "AnotherNewGame": [["anotherDataList", "*", "name"]],
    "NUUN_SaveScreen": [["ContentsList", "*", "ParamName"]],
    "ShopScene_Extension": [["NoneItemText"]],
    "EquipScene_Extension": [["RemoveEquipText"]],
    "MenuSubCommand": [["subCommands", "*", "Name"]],
    "TorigoyaMZ_CommonMenu": [["baseItems", "*", "name"]],
    "LL_MenuScreenCustom": [
        ["leftBlockLabel"],
        ["rightBlockLabel"],
        ["leftBottomBlockLabel"],
        ["rightBottomBlockLabel"],
        ["menuHelpTexts", "*", "helpText"],
    ],
    "CustomizeConfigItem": [["SwitchOptions", "*", "Name"]],
    "DarkPlasma_ItemStorage": [
        ["partyItemCountText"],
        ["storageItemCountText"],
    ],
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

# Scroll Text
EVENT_CODE_SCROLL_TEXT = 405

# MZ speaker name in Show Text. Other string parameters in command 101 are
# asset names and must not be translated.
EVENT_CODE_SHOW_TEXT_HEADER = 101

# Change Actor Name / Nickname / Profile
EVENT_CODE_ACTOR_TEXT_FIELDS = {320, 324, 325}

EVENT_CODE_PLUGIN_COMMAND = 357

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
            if candidate.is_dir() and any(
                path.name in DATABASE_TEXT_FIELDS
                or path.name == "System.json"
                or path.name in EVENT_FILE_NAMES
                or MAP_FILE_REGEX.match(path.name)
                for path in candidate.glob("*.json")
            ):
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
                or name == "System.json"
                or name in EVENT_FILE_NAMES
                or MAP_FILE_REGEX.match(name)
            ):
                files.append(str(path))

        plugins_file = data_dir.parent / "js" / "plugins.js"
        if plugins_file.is_file():
            files.append(str(plugins_file))

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

        entries: List[TranslationEntry] = []
        seen: Set[str] = set()

        name = path.name

        if name == "plugins.js":
            data, _, _ = self._load_plugins_file(path)
            self._extract_plugin_parameters(
                data=data,
                rel_path=rel_path,
                entries=entries,
                seen=seen,
            )
            return entries

        data = json.loads(path.read_text(encoding="utf-8"))

        if name == "System.json":
            self._extract_system(
                data=data,
                rel_path=rel_path,
                entries=entries,
                seen=seen,
            )

        elif name in DATABASE_TEXT_FIELDS:
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

    def _load_plugins_file(self, path: Path) -> tuple[List[Any], str, str]:
        text = path.read_text(encoding="utf-8-sig")
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < start:
            raise ValueError("Invalid RPG Maker plugins.js wrapper.")
        data = json.loads(text[start : end + 1])
        if not isinstance(data, list):
            raise ValueError("RPG Maker plugins.js must contain an array.")
        return data, text[:start], text[end + 1 :]

    @staticmethod
    def _decode_plugin_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped or stripped[0] not in "[{":
            return value
        try:
            decoded = json.loads(stripped)
        except (TypeError, ValueError):
            return value
        return decoded if isinstance(decoded, (dict, list)) else value

    def _extract_plugin_parameters(
        self,
        data: List[Any],
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        for plugin_index, plugin in enumerate(data):
            if not isinstance(plugin, dict) or not plugin.get("status"):
                continue
            plugin_name = plugin.get("name")
            parameters = plugin.get("parameters")
            patterns = PLUGIN_TEXT_FIELDS.get(plugin_name, [])
            if not isinstance(parameters, dict):
                continue
            for pattern in patterns:
                parameter = pattern[0]
                if parameter not in parameters:
                    continue
                self._walk_plugin_parameter(
                    value=parameters[parameter],
                    remaining=pattern[1:],
                    actual_path=[],
                    plugin_index=plugin_index,
                    plugin_name=plugin_name,
                    parameter=parameter,
                    rel_path=rel_path,
                    entries=entries,
                    seen=seen,
                )

    def _walk_plugin_parameter(
        self,
        value: Any,
        remaining: List[str],
        actual_path: List[Any],
        plugin_index: int,
        plugin_name: str,
        parameter: str,
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        decoded = self._decode_plugin_value(value)
        if decoded is not value:
            self._walk_plugin_parameter(
                decoded, remaining, actual_path, plugin_index, plugin_name,
                parameter, rel_path, entries, seen,
            )
            return

        if not remaining:
            if not isinstance(value, str) or not self._is_translatable(value):
                return
            location = {
                "type": "rpgmaker_plugin_parameter",
                "plugin_index": plugin_index,
                "parameter": parameter,
                "path": actual_path,
            }
            entry_id = make_entry_id(
                engine=self.engine_id,
                file_path=rel_path,
                location=location,
                source_text=value,
            )
            if entry_id in seen:
                return
            seen.add(entry_id)
            entries.append(
                TranslationEntry(
                    id=entry_id,
                    source_text=value,
                    context=f"{rel_path} -> {plugin_name}.{parameter}",
                    file_path=rel_path,
                    engine=self.engine_id,
                    location=location,
                    status=EntryStatus.PENDING,
                )
            )
            return

        token = remaining[0]
        rest = remaining[1:]
        if token == "*" and isinstance(value, list):
            for index, item in enumerate(value):
                self._walk_plugin_parameter(
                    item, rest, actual_path + [index], plugin_index,
                    plugin_name, parameter, rel_path, entries, seen,
                )
        elif isinstance(value, dict) and token in value:
            self._walk_plugin_parameter(
                value[token], rest, actual_path + [token], plugin_index,
                plugin_name, parameter, rel_path, entries, seen,
            )

    def _extract_system(
        self,
        data: Any,
        rel_path: str,
        entries: List[TranslationEntry],
        seen: Set[str],
    ) -> None:
        """Extract only player-visible labels from System.json."""
        if not isinstance(data, dict):
            return

        def add(value: Any, path: List[Any]) -> None:
            if not isinstance(value, str):
                return
            self._add_entry(
                entries=entries,
                seen=seen,
                source_text=value,
                rel_path=rel_path,
                json_path=path,
                context=f"{rel_path} -> " + ".".join(str(part) for part in path),
            )

        for field in SYSTEM_SCALAR_TEXT_FIELDS:
            add(data.get(field), [field])

        for field in SYSTEM_ARRAY_TEXT_FIELDS:
            values = data.get(field)
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                add(value, [field, index])

        terms = data.get("terms")
        if not isinstance(terms, dict):
            return

        for field in SYSTEM_TERM_ARRAY_FIELDS:
            values = terms.get(field)
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                add(value, ["terms", field, index])

        messages = terms.get("messages")
        if isinstance(messages, dict):
            for key, value in messages.items():
                add(value, ["terms", "messages", key])

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

            elif (
                code == EVENT_CODE_SCROLL_TEXT
                and isinstance(parameters, list)
                and parameters
                and isinstance(parameters[0], str)
            ):
                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=parameters[0],
                    rel_path=rel_path,
                    json_path=path + ["parameters", 0],
                    context=f"{rel_path} -> Scroll Text",
                )

            elif (
                code == EVENT_CODE_SHOW_TEXT_HEADER
                and isinstance(parameters, list)
                and len(parameters) > 4
                and isinstance(parameters[4], str)
            ):
                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=parameters[4],
                    rel_path=rel_path,
                    json_path=path + ["parameters", 4],
                    context=f"{rel_path} -> Speaker Name",
                )

            elif (
                code in EVENT_CODE_ACTOR_TEXT_FIELDS
                and isinstance(parameters, list)
                and len(parameters) > 1
                and isinstance(parameters[1], str)
            ):
                labels = {320: "Actor Name", 324: "Actor Nickname", 325: "Actor Profile"}
                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=parameters[1],
                    rel_path=rel_path,
                    json_path=path + ["parameters", 1],
                    context=f"{rel_path} -> {labels[code]}",
                )

            elif (
                code == EVENT_CODE_PLUGIN_COMMAND
                and isinstance(parameters, list)
                and len(parameters) > 3
                and parameters[0] == "TextPicture"
                and parameters[1] == "set"
                and isinstance(parameters[3], dict)
                and isinstance(parameters[3].get("text"), str)
            ):
                self._add_entry(
                    entries=entries,
                    seen=seen,
                    source_text=parameters[3]["text"],
                    rel_path=rel_path,
                    json_path=path + ["parameters", 3, "text"],
                    context=f"{rel_path} -> Text Picture",
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

        if source_path.name == "plugins.js":
            return self._inject_plugins_file(source_path, entries, target_path)

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

    def _inject_plugins_file(
        self,
        source_path: Path,
        entries: List[TranslationEntry],
        target_path: Path,
    ) -> int:
        data, prefix, suffix = self._load_plugins_file(source_path)
        changed = 0
        for entry in entries:
            if entry.ignored or entry.status == EntryStatus.ERROR or entry.target_text is None:
                continue
            location = entry.location or {}
            if location.get("type") != "rpgmaker_plugin_parameter":
                continue
            try:
                plugin = data[int(location["plugin_index"])]
                parameter = str(location["parameter"])
                parameters = plugin["parameters"]
                parameters[parameter] = self._replace_plugin_value(
                    parameters[parameter],
                    list(location.get("path") or []),
                    entry.target_text,
                )
                changed += 1
            except (IndexError, KeyError, TypeError, ValueError):
                continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = prefix + json.dumps(data, ensure_ascii=False, indent=2) + suffix
        target_path.write_text(rendered, encoding="utf-8")
        return changed

    def _replace_plugin_value(
        self,
        value: Any,
        path: List[Any],
        replacement: str,
    ) -> Any:
        if not path:
            return replacement

        decoded = self._decode_plugin_value(value)
        if decoded is not value:
            updated = self._replace_plugin_value(decoded, path, replacement)
            return json.dumps(updated, ensure_ascii=False, separators=(",", ":"))

        key = path[0]
        if isinstance(value, list) and isinstance(key, int):
            value[key] = self._replace_plugin_value(value[key], path[1:], replacement)
            return value
        if isinstance(value, dict) and key in value:
            value[key] = self._replace_plugin_value(value[key], path[1:], replacement)
            return value
        raise KeyError(key)

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
