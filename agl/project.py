from __future__ import annotations

import csv
import datetime
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import EntryStatus, TranslationEntry
from .io_utils import atomic_write_text


PROJECT_SCHEMA_VERSION = 2


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
        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
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

        atomic_write_text(
            project_file,
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

        return ProjectStore(project_dir)

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def save_meta(self) -> None:
        self.meta["updated_at"] = _now()
        atomic_write_text(
            self.project_file,
            json.dumps(self.meta, ensure_ascii=False, indent=2),
        )

    # ------------------------------------------------------------------
    # SQLite schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        existing_tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "entries" in existing_tables and "schema_meta" not in existing_tables:
            backup_path = self.db_path.with_suffix(".pre_v2.db")
            if not backup_path.exists():
                backup_conn = sqlite3.connect(str(backup_path))
                try:
                    self.conn.backup(backup_conn)
                finally:
                    backup_conn.close()

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        version_row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current_version = int(version_row[0]) if version_row else 0
        if current_version > PROJECT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Project database schema {current_version} is newer than "
                f"this application supports ({PROJECT_SCHEMA_VERSION})."
            )

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
                origin_key TEXT,
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
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "origin_key" not in columns:
            self.conn.execute("ALTER TABLE entries ADD COLUMN origin_key TEXT")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                action TEXT NOT NULL,
                changed_fields TEXT NOT NULL,
                source_text TEXT NOT NULL,
                target_text TEXT,
                status TEXT NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0,
                ignored INTEGER NOT NULL DEFAULT 0,
                fuzzy INTEGER NOT NULL DEFAULT 0,
                machine_translated INTEGER NOT NULL DEFAULT 0,
                human_reviewed INTEGER NOT NULL DEFAULT 0,
                note TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_revisions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                label TEXT NOT NULL,
                entry_count INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS revision_entries (
                revision_id TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                source_text TEXT NOT NULL,
                target_text TEXT,
                context TEXT,
                file_path TEXT,
                engine TEXT,
                location_json TEXT,
                status TEXT NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0,
                ignored INTEGER NOT NULL DEFAULT 0,
                fuzzy INTEGER NOT NULL DEFAULT 0,
                machine_translated INTEGER NOT NULL DEFAULT 0,
                human_reviewed INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                PRIMARY KEY (revision_id, entry_id),
                FOREIGN KEY (revision_id) REFERENCES project_revisions(id) ON DELETE CASCADE
            )
            """
        )

        rows = self.conn.execute(
            "SELECT id, engine, file_path, location_json, source_text FROM entries WHERE origin_key IS NULL OR origin_key = ''"
        ).fetchall()
        for row in rows:
            origin = self.make_origin_key(row[1], row[2], row[3])
            if not row[3] or row[3] == '{}':
                origin = hashlib.sha256("|".join([row[1] or "", row[2] or "", "source", row[4] or ""]).encode("utf-8")).hexdigest()[:32]
            self.conn.execute(
                "UPDATE entries SET origin_key = ? WHERE id = ?",
                (origin, row[0]),
            )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_origin_key ON entries(origin_key)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_history_entry ON entry_history(entry_id, id DESC)")
        self.conn.execute(
            """
            INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(PROJECT_SCHEMA_VERSION),),
        )
        self.conn.commit()

    @staticmethod
    def make_origin_key(engine: str, file_path: str, location: Any) -> str:
        if isinstance(location, str):
            location_text = location
            has_location = bool(location.strip())
        else:
            location_obj = location or {}
            location_text = json.dumps(location_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            has_location = bool(location_obj)
        # Test/manual entries sometimes have no location. Keep those distinct by
        # including source_text in the fallback identity; real engine entries
        # normally carry a stable location and can therefore survive source changes.
        return hashlib.sha256("|".join([engine or "", file_path or "", location_text]).encode("utf-8")).hexdigest()[:32]

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

    def _get_by_origin_key(self, origin_key: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.execute("SELECT * FROM entries WHERE origin_key = ? ORDER BY updated_at DESC LIMIT 1", (origin_key,))
        return cursor.fetchone()

    def _record_history(self, row: sqlite3.Row, action: str, changed_fields: list[str], changed_at: Optional[str] = None) -> None:
        self.conn.execute(
            """
            INSERT INTO entry_history (
                entry_id, changed_at, action, changed_fields, source_text, target_text, status,
                locked, ignored, fuzzy, machine_translated, human_reviewed, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], changed_at or _now(), action, json.dumps(changed_fields, ensure_ascii=False),
                row["source_text"], row["target_text"], row["status"], row["locked"], row["ignored"],
                row["fuzzy"], row["machine_translated"], row["human_reviewed"], row["note"],
            ),
        )

    def history(self, entry_id: Optional[str] = None, limit: int = 100) -> list[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if entry_id:
            cursor = self.conn.execute(
                "SELECT * FROM entry_history WHERE entry_id = ? ORDER BY id DESC LIMIT ?",
                (entry_id, limit),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM entry_history ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        items = []
        for row in cursor.fetchall():
            item = dict(row)
            try:
                item["changed_fields"] = json.loads(item["changed_fields"])
            except Exception:
                item["changed_fields"] = []
            items.append(item)
        return items

    def create_revision(self, label: str = "Checkpoint") -> Dict[str, Any]:
        revision_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        created_at = _now()
        entries = self.all_entries()
        self.conn.execute(
            "INSERT INTO project_revisions (id, created_at, label, entry_count) VALUES (?, ?, ?, ?)",
            (revision_id, created_at, (label or "Checkpoint").strip()[:120] or "Checkpoint", len(entries)),
        )
        for entry in entries:
            self.conn.execute(
                """
                INSERT INTO revision_entries (revision_id, entry_id, source_text, target_text, context, file_path, engine,
                    location_json, status, locked, ignored, fuzzy, machine_translated, human_reviewed, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id, entry.id, entry.source_text, entry.target_text, entry.context, entry.file_path, entry.engine,
                    json.dumps(entry.location or {}, ensure_ascii=False), entry.status.value, int(entry.locked), int(entry.ignored),
                    int(entry.fuzzy), int(entry.machine_translated), int(entry.human_reviewed), entry.note,
                ),
            )
        self.conn.commit()
        return {"id": revision_id, "created_at": created_at, "label": label or "Checkpoint", "entry_count": len(entries)}

    def list_revisions(self, limit: int = 50) -> list[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        rows = self.conn.execute("SELECT id, created_at, label, entry_count FROM project_revisions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def revision_diff(self, revision_id: str, limit: int = 100) -> Dict[str, Any]:
        revision = self.conn.execute("SELECT * FROM project_revisions WHERE id = ?", (revision_id,)).fetchone()
        if not revision:
            raise ValueError("Revision not found.")
        current = {row["id"]: row for row in self.conn.execute("SELECT * FROM entries").fetchall()}
        snap = {row["entry_id"]: row for row in self.conn.execute("SELECT * FROM revision_entries WHERE revision_id = ?", (revision_id,)).fetchall()}
        added = [eid for eid in current.keys() - snap.keys()]
        removed = [eid for eid in snap.keys() - current.keys()]
        changed = []
        for eid in current.keys() & snap.keys():
            a, b = snap[eid], current[eid]
            fields = []
            for field in ("source_text", "target_text", "status", "locked", "ignored", "fuzzy", "machine_translated", "human_reviewed", "note"):
                if a[field] != b[field]:
                    fields.append(field)
            if fields:
                changed.append({"entry_id": eid, "fields": fields, "source_before": a["source_text"], "source_after": b["source_text"], "target_before": a["target_text"] or "", "target_after": b["target_text"] or "", "status_before": a["status"], "status_after": b["status"]})
        return {"revision": dict(revision), "summary": {"added": len(added), "removed": len(removed), "changed": len(changed), "unchanged": max(0, len(current) - len(added) - len(changed))}, "items": [{"kind":"added","entry_id":x} for x in added[:limit]] + [{"kind":"removed","entry_id":x} for x in removed[:limit]] + [{"kind":"changed", **x} for x in changed[:limit]]}

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
        incoming_origin = self.make_origin_key(entry.engine, entry.file_path, entry.location)
        if not entry.location:
            incoming_origin = hashlib.sha256("|".join([entry.engine or "", entry.file_path or "", "source", (entry.source_text or "")]).encode("utf-8")).hexdigest()[:32]
        row = self._get_row(entry.id)
        if row is None:
            row = self._get_by_origin_key(incoming_origin)
        now = _now()
        location_json = json.dumps(entry.location or {}, ensure_ascii=False)

        if row is None:
            self.conn.execute(
                """
                INSERT INTO entries (id, source_text, target_text, context, file_path, engine, location_json, origin_key,
                    status, locked, ignored, fuzzy, machine_translated, human_reviewed, note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entry.id, entry.source_text, entry.target_text, entry.context, entry.file_path, entry.engine,
                 location_json, incoming_origin, entry.status.value, int(entry.locked), int(entry.ignored), int(entry.fuzzy),
                 int(entry.machine_translated), int(entry.human_reviewed), entry.note, now),
            )
            return

        if row["source_text"] != entry.source_text:
            self._record_history(row, "source_changed", ["source_text", "status", "fuzzy"], now)
            next_status = row["status"]
            if row["locked"]:
                next_status = EntryStatus.LOCKED.value
            elif row["target_text"]:
                next_status = EntryStatus.NEEDS_REVIEW.value
            else:
                next_status = EntryStatus.PENDING.value
            self.conn.execute(
                """UPDATE entries SET source_text=?, context=?, file_path=?, engine=?, location_json=?, origin_key=?,
                    status=?, fuzzy=1, human_reviewed=0, updated_at=? WHERE id=?""",
                (entry.source_text, entry.context, entry.file_path, entry.engine, location_json, incoming_origin, next_status, now, row["id"]),
            )
        else:
            self.conn.execute(
                """UPDATE entries SET context=?, file_path=?, engine=?, location_json=?, origin_key=?, updated_at=? WHERE id=?""",
                (entry.context, entry.file_path, entry.engine, location_json, incoming_origin, now, row["id"]),
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
        before = self._get_row(entry_id)
        if before is None:
            return False
        fields: List[str] = []
        params: List[Any] = []
        changed_fields: list[str] = []

        def add(name: str, value: Any, current: Any) -> None:
            if value is not None and value != current:
                fields.append(f"{name} = ?")
                params.append(value)
                changed_fields.append(name)

        if target_text is not None: add("target_text", target_text, before["target_text"])
        status_value = status.value if isinstance(status, EntryStatus) else (str(status) if status is not None else None)
        if status_value is not None: add("status", status_value, before["status"])
        if locked is not None: add("locked", int(bool(locked)), before["locked"])
        if ignored is not None: add("ignored", int(bool(ignored)), before["ignored"])
        if fuzzy is not None: add("fuzzy", int(bool(fuzzy)), before["fuzzy"])
        if machine_translated is not None: add("machine_translated", int(bool(machine_translated)), before["machine_translated"])
        if human_reviewed is not None: add("human_reviewed", int(bool(human_reviewed)), before["human_reviewed"])
        if note is not None: add("note", note, before["note"])
        if not fields:
            return False

        now = _now()
        self._record_history(before, "entry_updated", changed_fields, now)
        fields.append("updated_at = ?"); params.append(now); params.append(entry_id)
        cursor = self.conn.execute(f"UPDATE entries SET {', '.join(fields)} WHERE id = ?", tuple(params))
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
