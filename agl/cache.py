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

        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA journal_mode = WAL")
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

    def get_exact_any(
        self,
        source_text: str,
        target_language: str,
        prefer_human_reviewed: bool = True,
    ) -> Optional[dict]:
        """Return the most recently updated exact source/target-language memory item."""
        order = "human_reviewed DESC, updated_at DESC" if prefer_human_reviewed else "updated_at DESC"
        cursor = self.conn.execute(
            f"""
            SELECT source_text, target_language, translated_text, provider, model,
                   human_reviewed, updated_at
            FROM translation_cache
            WHERE source_text = ? AND target_language = ?
            ORDER BY {order}
            LIMIT 1
            """,
            ((source_text or "").strip(), target_language or ""),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_human_reviewed(
        self,
        source_text: str,
        target_language: str,
    ) -> Optional[str]:
        cursor = self.conn.execute(
            """
            SELECT translated_text
            FROM translation_cache
            WHERE source_text = ? AND target_language = ? AND human_reviewed = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            ((source_text or "").strip(), target_language or ""),
        )
        row = cursor.fetchone()
        return row["translated_text"] if row else None

    def search(
        self,
        query: str = "",
        target_language: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Search recent translation memory entries without exposing cache hashes."""
        query = (query or "").strip()
        limit = max(1, min(int(limit), 200))
        clauses = []
        params = []
        if query:
            like = f"%{query}%"
            clauses.append("(source_text LIKE ? OR translated_text LIKE ?)")
            params.extend([like, like])
        if target_language:
            clauses.append("target_language = ?")
            params.append(target_language)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cursor = self.conn.execute(
            f"""
            SELECT source_text, target_language, translated_text, provider, model,
                   human_reviewed, updated_at
            FROM translation_cache
            {where}
            ORDER BY human_reviewed DESC, updated_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

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
