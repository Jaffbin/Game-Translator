from __future__ import annotations

import csv
import datetime
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import EntryStatus, TranslationEntry


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


class ProjectStore:
    """
    Translation project storage.

    Project directory structure:

      MyGame_zh/
        project.json
        entries.db
    """

    def __init__(self, project_dir: Path | str):
        self.project_dir = Path(project_dir)
        self.project_file = self.project_dir / "project.json"

        if not self.project_file.exists():
            raise FileNotFoundError(
                f"Project file not found: {self.project_file}"
            )

        self.meta: Dict[str, Any] = json.loads(
            self.project_file.read_text(encoding="utf-8")
        )

        self.db_path = self.project_dir / "entries.db"
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._closed = False

        self._init_schema()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ProjectStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------------
    # Project creation
    # ------------------------------------------------------------------

    @staticmethod
    def create(
        project_dir: Path | str,
        game_path: Path | str,
        engine_id: str,
        target_language: str,
        name: Optional[str] = None,
    ) -> "ProjectStore":
        project_dir = Path(project_dir)
        project_file = project_dir / "project.json"

        if project_file.exists():
            raise FileExistsError(
                f"Project already exists: {project_file}"
            )

        project_dir.mkdir(parents=True, exist_ok=True)

        game_path = Path(game_path).resolve()

        meta = {
            "project_id": uuid.uuid4().hex[:16],
            "name": name or project_dir.name,
            "game_path": str(game_path),
            "engine": engine_id,
            "target_language": target_language,
            "created_at": _now(),
            "updated_at": _now(),
        }

        project_file.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return ProjectStore(project_dir)

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def save_meta(self) -> None:
        self.meta["updated_at"] = _now()
        self.project_file.write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # SQLite schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                target_text TEXT,
                context TEXT,
                file_path TEXT,
                engine TEXT,
                location_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                locked INTEGER NOT NULL DEFAULT 0,
                ignored INTEGER NOT NULL DEFAULT 0,
                fuzzy INTEGER NOT NULL DEFAULT 0,
                machine_translated INTEGER NOT NULL DEFAULT 0,
                human_reviewed INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Entry conversion
    # ------------------------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> TranslationEntry:
        try:
            status = EntryStatus(row["status"])
        except ValueError:
            status = EntryStatus.PENDING

        try:
            location = json.loads(row["location_json"] or "{}")
        except json.JSONDecodeError:
            location = {}

        return TranslationEntry(
            id=row["id"],
            source_text=row["source_text"],
            target_text=row["target_text"],
            context=row["context"] or "",
            file_path=row["file_path"] or "",
            engine=row["engine"] or "",
            location=location,
            status=status,
            locked=bool(row["locked"]),
            ignored=bool(row["ignored"]),
            fuzzy=bool(row["fuzzy"]),
            machine_translated=bool(row["machine_translated"]),
            human_reviewed=bool(row["human_reviewed"]),
            note=row["note"] or "",
        )

    def _get_row(self, entry_id: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.execute(
            """
            SELECT *
            FROM entries
            WHERE id = ?
            """,
            (entry_id,),
        )
        return cursor.fetchone()

    def get_entry(self, entry_id: str) -> Optional[TranslationEntry]:
        row = self._get_row(entry_id)
        if row is None:
            return None
        return self._row_to_entry(row)

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def upsert_entries(self, entries: List[TranslationEntry]) -> int:
        count = 0
        for entry in entries:
            self._upsert_entry(entry)
            count += 1

        self.conn.commit()
        return count

    def _upsert_entry(self, entry: TranslationEntry) -> None:
        row = self._get_row(entry.id)
        now = _now()

        location_json = json.dumps(
            entry.location or {},
            ensure_ascii=False,
        )

        if row is None:
            self.conn.execute(
                """
                INSERT INTO entries (
                    id,
                    source_text,
                    target_text,
                    context,
                    file_path,
                    engine,
                    location_json,
                    status,
                    locked,
                    ignored,
                    fuzzy,
                    machine_translated,
                    human_reviewed,
                    note,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.source_text,
                    entry.target_text,
                    entry.context,
                    entry.file_path,
                    entry.engine,
                    location_json,
                    entry.status.value,
                    int(entry.locked),
                    int(entry.ignored),
                    int(entry.fuzzy),
                    int(entry.machine_translated),
                    int(entry.human_reviewed),
                    entry.note,
                    now,
                ),
            )
            return

        if row["source_text"] != entry.source_text:
            # Source text changed. Keep old target, but mark fuzzy.
            self.conn.execute(
                """
                UPDATE entries
                SET
                    source_text = ?,
                    context = ?,
                    file_path = ?,
                    engine = ?,
                    location_json = ?,
                    fuzzy = 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    entry.source_text,
                    entry.context,
                    entry.file_path,
                    entry.engine,
                    location_json,
                    now,
                    entry.id,
                ),
            )
        else:
            # Keep translation and review state.
            self.conn.execute(
                """
                UPDATE entries
                SET
                    context = ?,
                    file_path = ?,
                    engine = ?,
                    location_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    entry.context,
                    entry.file_path,
                    entry.engine,
                    location_json,
                    now,
                    entry.id,
                ),
            )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def all_entries(
        self,
        file_path: Optional[str] = None,
        status: Optional[EntryStatus | str] = None,
    ) -> List[TranslationEntry]:
        query = "SELECT * FROM entries"
        conditions = []
        params: List[Any] = []

        if file_path is not None:
            conditions.append("file_path = ?")
            params.append(file_path)

        if status is not None:
            if isinstance(status, EntryStatus):
                status_value = status.value
            else:
                status_value = str(status)

            conditions.append("status = ?")
            params.append(status_value)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY file_path, id"

        cursor = self.conn.execute(query, tuple(params))
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def stats_by_status(self) -> Dict[str, int]:
        counts = {status.value: 0 for status in EntryStatus}

        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM entries
            GROUP BY status
            """
        )

        for row in cursor.fetchall():
            counts[row["status"]] = row["count"]

        return counts

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_entry(
        self,
        entry_id: str,
        target_text: Optional[str] = None,
        status: Optional[EntryStatus | str] = None,
        locked: Optional[bool] = None,
        ignored: Optional[bool] = None,
        fuzzy: Optional[bool] = None,
        machine_translated: Optional[bool] = None,
        human_reviewed: Optional[bool] = None,
        note: Optional[str] = None,
    ) -> bool:
        fields: List[str] = []
        params: List[Any] = []

        if target_text is not None:
            fields.append("target_text = ?")
            params.append(target_text)

        if status is not None:
            if isinstance(status, EntryStatus):
                status_value = status.value
            else:
                status_value = str(status)

            fields.append("status = ?")
            params.append(status_value)

        if locked is not None:
            fields.append("locked = ?")
            params.append(int(bool(locked)))

        if ignored is not None:
            fields.append("ignored = ?")
            params.append(int(bool(ignored)))

        if fuzzy is not None:
            fields.append("fuzzy = ?")
            params.append(int(bool(fuzzy)))

        if machine_translated is not None:
            fields.append("machine_translated = ?")
            params.append(int(bool(machine_translated)))

        if human_reviewed is not None:
            fields.append("human_reviewed = ?")
            params.append(int(bool(human_reviewed)))

        if note is not None:
            fields.append("note = ?")
            params.append(note)

        if not fields:
            return False

        fields.append("updated_at = ?")
        params.append(_now())

        params.append(entry_id)

        sql = f"""
            UPDATE entries
            SET {", ".join(fields)}
            WHERE id = ?
        """

        cursor = self.conn.execute(sql, tuple(params))
        self.conn.commit()

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(
        self,
        output_path: Path | str,
        status: Optional[EntryStatus | str] = None,
    ) -> int:
        entries = self.all_entries(status=status)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            writer = csv.writer(f)

            writer.writerow(
                [
                    "entry_id",
                    "file_path",
                    "context",
                    "status",
                    "locked",
                    "ignored",
                    "source_text",
                    "target_text",
                    "note",
                ]
            )

            for entry in entries:
                writer.writerow(
                    [
                        entry.id,
                        entry.file_path,
                        entry.context,
                        entry.status.value,
                        int(entry.locked),
                        int(entry.ignored),
                        entry.source_text,
                        entry.target_text or "",
                        entry.note,
                    ]
                )

        return len(entries)

    # ------------------------------------------------------------------
    # CSV import
    # ------------------------------------------------------------------

    def import_csv(
        self,
        input_path: Path | str,
        overwrite_locked: bool = False,
    ) -> Dict[str, int]:
        stats = {
            "updated": 0,
            "unchanged": 0,
            "missing": 0,
            "locked": 0,
            "invalid": 0,
        }

        input_path = Path(input_path)

        with input_path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            reader = csv.DictReader(f)

            for row in reader:
                entry_id = row.get("entry_id") or row.get("id")

                if not entry_id:
                    stats["invalid"] += 1
                    continue

                entry = self.get_entry(entry_id)

                if entry is None:
                    stats["missing"] += 1
                    continue

                if entry.locked and not overwrite_locked:
                    stats["locked"] += 1
                    continue

                updates: Dict[str, Any] = {}

                target_text = row.get("target_text")

                if (
                    target_text is not None
                    and target_text != ""
                    and target_text != (entry.target_text or "")
                ):
                    updates["target_text"] = target_text
                    updates["status"] = EntryStatus.REVIEWED
                    updates["human_reviewed"] = True
                    updates["machine_translated"] = False

                status_text = row.get("status")
                if status_text and "status" not in updates:
                    try:
                        updates["status"] = EntryStatus(status_text)
                    except ValueError:
                        pass

                locked_text = row.get("locked")
                if locked_text is not None and locked_text.strip() != "":
                    updates["locked"] = _parse_bool(locked_text)

                ignored_text = row.get("ignored")
                if ignored_text is not None and ignored_text.strip() != "":
                    updates["ignored"] = _parse_bool(ignored_text)

                note_text = row.get("note")
                if note_text is not None and note_text != entry.note:
                    updates["note"] = note_text

                if updates:
                    self.update_entry(entry_id, **updates)
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1

        self.conn.commit()
        return stats

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(self) -> None:
        if not self._closed:
            self.conn.close()
            self._closed = True