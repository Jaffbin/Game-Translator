from __future__ import annotations

import datetime
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..engines.rpgmaker import RPGMakerHandler
from ..installer import sha256_file
from ..io_utils import atomic_write_text
from ..project import ProjectStore
from ..version import __version__


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    value_type: type
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    index: Optional[int] = None


PARAM_LABELS = [
    "Max HP", "Max MP", "Attack", "Defense",
    "Magic Attack", "Magic Defense", "Agility", "Luck",
]


def _param_fields(minimum: int = -999999, maximum: int = 999999) -> List[FieldSpec]:
    return [
        FieldSpec(f"param_{index}", label, int, minimum, maximum, index=index)
        for index, label in enumerate(PARAM_LABELS)
    ]


CATEGORY_FILES = {
    "actors": "Actors.json",
    "items": "Items.json",
    "weapons": "Weapons.json",
    "armors": "Armors.json",
    "enemies": "Enemies.json",
    "skills": "Skills.json",
    "states": "States.json",
}

CATEGORY_FIELDS: Dict[str, List[FieldSpec]] = {
    "actors": [
        FieldSpec("initialLevel", "Initial Level", int, 1, 999),
        FieldSpec("maxLevel", "Maximum Level", int, 1, 999),
    ],
    "items": [
        FieldSpec("price", "Price", int, 0, 99999999),
        FieldSpec("consumable", "Consumable", bool),
        FieldSpec("speed", "Speed", int, -2000, 2000),
        FieldSpec("successRate", "Success Rate", int, 0, 100),
        FieldSpec("repeats", "Repeats", int, 1, 99),
        FieldSpec("tpGain", "TP Gain", int, 0, 999),
    ],
    "weapons": [FieldSpec("price", "Price", int, 0, 99999999), *_param_fields()],
    "armors": [FieldSpec("price", "Price", int, 0, 99999999), *_param_fields()],
    "enemies": [
        FieldSpec("exp", "Experience", int, 0, 999999999),
        FieldSpec("gold", "Gold", int, 0, 999999999),
        *_param_fields(0, 9999999),
    ],
    "skills": [
        FieldSpec("mpCost", "MP Cost", int, 0, 999999),
        FieldSpec("tpCost", "TP Cost", int, 0, 100),
        FieldSpec("speed", "Speed", int, -2000, 2000),
        FieldSpec("successRate", "Success Rate", int, 0, 100),
        FieldSpec("repeats", "Repeats", int, 1, 99),
        FieldSpec("tpGain", "TP Gain", int, 0, 999),
    ],
    "states": [
        FieldSpec("priority", "Priority", int, 0, 100),
        FieldSpec("restriction", "Restriction", int, 0, 4),
        FieldSpec("removeAtBattleEnd", "Remove At Battle End", bool),
        FieldSpec("autoRemovalTiming", "Auto Removal Timing", int, 0, 2),
        FieldSpec("minTurns", "Minimum Turns", int, 0, 999),
        FieldSpec("maxTurns", "Maximum Turns", int, 0, 999),
    ],
}


def categories() -> List[Dict[str, Any]]:
    return [
        {
            "id": category,
            "file": CATEGORY_FILES[category],
            "fields": [
                {
                    "key": spec.key,
                    "label": spec.label,
                    "type": "boolean" if spec.value_type is bool else "integer",
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                }
                for spec in CATEGORY_FIELDS[category]
            ],
        }
        for category in CATEGORY_FILES
    ]


def _game_data_dir(game_path: Path | str) -> Path:
    data_dir = RPGMakerHandler()._data_dir(str(game_path))
    if data_dir is None:
        raise RuntimeError("RPG Maker MV/MZ data directory was not found.")
    return data_dir


def _load_records(game_path: Path | str, category: str) -> tuple[Path, List[Any]]:
    if category not in CATEGORY_FILES:
        raise ValueError(f"Unsupported modifier category: {category}")
    path = _game_data_dir(game_path) / CATEGORY_FILES[category]
    if not path.is_file():
        raise FileNotFoundError(f"RPG Maker database file not found: {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"RPG Maker database file must contain an array: {path.name}")
    return path, data


def _read_value(record: Dict[str, Any], spec: FieldSpec) -> Any:
    if spec.index is None:
        return record.get(spec.key)
    params = record.get("params")
    if not isinstance(params, list) or spec.index >= len(params):
        return None
    return params[spec.index]


def catalog(
    game_path: Path | str,
    category: str,
    query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    _, records = _load_records(game_path, category)
    specs = CATEGORY_FIELDS[category]
    query = (query or "").casefold()
    items = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), int):
            continue
        name = str(record.get("name") or f"#{record['id']}")
        if query and query not in name.casefold() and query not in str(record["id"]):
            continue
        items.append(
            {
                "id": record["id"],
                "name": name,
                "values": {spec.key: _read_value(record, spec) for spec in specs},
            }
        )
    total = len(items)
    return {"category": category, "total": total, "items": items[offset : offset + limit]}


def _validated_value(spec: FieldSpec, value: Any) -> Any:
    if spec.value_type is bool:
        if type(value) is not bool:
            raise ValueError(f"{spec.key} must be a boolean.")
        return value
    if type(value) is not int:
        raise ValueError(f"{spec.key} must be an integer.")
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"{spec.key} must be at least {spec.minimum}.")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.key} must be at most {spec.maximum}.")
    return value


def _set_value(record: Dict[str, Any], spec: FieldSpec, value: Any) -> None:
    if spec.index is None:
        record[spec.key] = value
        return
    params = record.get("params")
    if not isinstance(params, list) or spec.index >= len(params):
        raise ValueError(f"Record does not contain {spec.key}.")
    params[spec.index] = value


def _normalize_changes(changes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for raw in changes:
        if not isinstance(raw, dict):
            raise ValueError("Each modifier change must be an object.")
        category = str(raw.get("category") or "").strip().lower()
        record_id = raw.get("record_id")
        values = raw.get("values")
        if category not in CATEGORY_FIELDS:
            raise ValueError(f"Unsupported modifier category: {category}")
        if type(record_id) is not int or record_id <= 0:
            raise ValueError("record_id must be a positive integer.")
        if not isinstance(values, dict) or not values:
            raise ValueError("Modifier values cannot be empty.")
        specs = {spec.key: spec for spec in CATEGORY_FIELDS[category]}
        clean_values = {}
        for key, value in values.items():
            if key not in specs:
                raise ValueError(f"Field is not safely editable for {category}: {key}")
            clean_values[key] = _validated_value(specs[key], value)
        normalized.append({"category": category, "record_id": record_id, "values": clean_values})
    if not normalized:
        raise ValueError("No modifier changes supplied.")
    if len(normalized) > 200:
        raise ValueError("A modifier patch may contain at most 200 record changes.")
    return normalized


def preview(game_path: Path | str, changes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = _normalize_changes(changes)
    loaded: Dict[str, List[Any]] = {}
    result = []
    for change in normalized:
        category = change["category"]
        if category not in loaded:
            _, loaded[category] = _load_records(game_path, category)
        records = loaded[category]
        record = next(
            (item for item in records if isinstance(item, dict) and item.get("id") == change["record_id"]),
            None,
        )
        if record is None:
            raise ValueError(f"Record {change['record_id']} was not found in {category}.")
        specs = {spec.key: spec for spec in CATEGORY_FIELDS[category]}
        fields = []
        for key, after in change["values"].items():
            before = _read_value(record, specs[key])
            if before != after:
                fields.append({"field": key, "label": specs[key].label, "before": before, "after": after})
                _set_value(record, specs[key], after)
        if fields:
            result.append(
                {
                    "category": category,
                    "record_id": change["record_id"],
                    "name": str(record.get("name") or f"#{change['record_id']}"),
                    "fields": fields,
                }
            )
    return {"changes": result, "records_changed": len(result), "fields_changed": sum(len(x["fields"]) for x in result)}


def build_patch(
    project_dir: Path | str,
    changes: Iterable[Dict[str, Any]],
    patch_name: Optional[str] = None,
) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    with ProjectStore(project_dir) as store:
        if store.meta.get("engine") != "rpgmaker_mv_mz":
            raise RuntimeError("RPG Maker data mods require an RPG Maker MV/MZ project.")
        game_root = Path(store.meta["game_path"]).resolve()
        project_id = store.meta.get("project_id")
        project_name = store.meta.get("name")

    plan = preview(game_root, changes)
    if not plan["changes"]:
        raise ValueError("The modifier values are unchanged; no patch was created.")
    normalized = _normalize_changes(changes)
    if patch_name is None:
        patch_name = "mod_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", patch_name):
        raise ValueError("patch_name must be one safe filename component.")
    patch_dir = project_dir / "patches" / patch_name
    if patch_dir.exists():
        raise FileExistsError(f"Patch directory already exists: {patch_dir}")
    patch_dir.mkdir(parents=True)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for change in normalized:
        grouped.setdefault(change["category"], []).append(change)
    manifest_files = []
    try:
        for category, category_changes in grouped.items():
            original_file, records = _load_records(game_root, category)
            specs = {spec.key: spec for spec in CATEGORY_FIELDS[category]}
            changed_fields = 0
            for change in category_changes:
                record = next(
                    (item for item in records if isinstance(item, dict) and item.get("id") == change["record_id"]),
                    None,
                )
                if record is None:
                    raise ValueError(f"Record {change['record_id']} was not found in {category}.")
                for key, value in change["values"].items():
                    if _read_value(record, specs[key]) != value:
                        _set_value(record, specs[key], value)
                        changed_fields += 1
            if not changed_fields:
                continue
            rel_path = original_file.relative_to(game_root).as_posix()
            patched_file = patch_dir / rel_path
            patched_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                patched_file,
                json.dumps(records, ensure_ascii=False, separators=(",", ":")),
            )
            manifest_files.append(
                {
                    "original_path": rel_path,
                    "patched_path": rel_path,
                    "original_sha256": sha256_file(original_file),
                    "patched_sha256": sha256_file(patched_file),
                    "changed_entries": changed_fields,
                }
            )

        manifest = {
            "tool": "AutoGame Localizer",
            "version": __version__,
            "kind": "rpgmaker_data_mod",
            "project_id": project_id,
            "project_name": project_name,
            "engine": "rpgmaker_mv_mz",
            "game_path": str(game_root),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "files": manifest_files,
        }
        atomic_write_text(
            patch_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        atomic_write_text(
            patch_dir / "mod_plan.json",
            json.dumps(plan, ensure_ascii=False, indent=2),
        )
        return {
            "patch_dir": str(patch_dir.resolve()),
            "files": len(manifest_files),
            **plan,
        }
    except Exception:
        shutil.rmtree(patch_dir, ignore_errors=True)
        raise
