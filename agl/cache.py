from __future__ import annotations

import datetime
import hashlib
import sqlite3
from pathlib import Path
from typing import Optional


def _source_hash(
    source_text: str,
    target_language: str,
    namespace: str = "",
) -> str:
    payload = "\n".join(
        [
            (source_text or "").strip(),
            target_language or "",
            namespace or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TranslationMemory:
    """
    SQLite-backed translation memory.

    Rules:
      - Same source_text + target_language reuses translation.
      - Human-reviewed entries are not overwritten by machine translations.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._closed = False

        self._init_schema()

    def __enter__(self) -> "TranslationMemory":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_cache (
                source_hash TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                target_language TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                human_reviewed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def get(self, source_text: str, target_language: str, namespace: str = "",) -> Optional[str]:
        key = _source_hash(source_text, target_language, namespace)
        cursor = self.conn.execute(
            """
            SELECT translated_text
            FROM translation_cache
            WHERE source_hash = ?
            """,
            (key,),
        )
        row = cursor.fetchone()
        return row["translated_text"] if row else None

    def put(
        self,
        source_text: str,
        target_language: str,
        translated_text: str,
        provider: str = "",
        model: str = "",
        human_reviewed: bool = False,
        namespace: str = "",
    ) -> None:
        key = _source_hash(source_text, target_language, namespace)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if not human_reviewed:
            cursor = self.conn.execute(
                """
                SELECT human_reviewed
                FROM translation_cache
                WHERE source_hash = ?
                """,
                (key,),
            )
            row = cursor.fetchone()
            if row and row["human_reviewed"]:
                # Do not overwrite human-reviewed translation with machine output.
                return

        self.conn.execute(
            """
            INSERT INTO translation_cache (
                source_hash,
                source_text,
                target_language,
                translated_text,
                provider,
                model,
                human_reviewed,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_hash) DO UPDATE SET
                source_text = excluded.source_text,
                target_language = excluded.target_language,
                translated_text = excluded.translated_text,
                provider = excluded.provider,
                model = excluded.model,
                human_reviewed = excluded.human_reviewed,
                updated_at = excluded.updated_at
            """,
            (
                key,
                source_text,
                target_language,
                translated_text,
                provider,
                model,
                int(bool(human_reviewed)),
                now,
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        if not self._closed:
            self.conn.close()
            self._closed = True