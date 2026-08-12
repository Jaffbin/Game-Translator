from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class EntryStatus(str, Enum):
    PENDING = "pending"
    MACHINE_TRANSLATED = "machine_translated"
    NEEDS_REVIEW = "needs_review"
    REVIEWED = "reviewed"
    LOCKED = "locked"
    IGNORED = "ignored"
    ERROR = "error"


def normalize_text(text: str) -> str:
    """
    Normalize whitespace for hashing and dedupe.
    Do not use this as the final display text.
    """
    return " ".join(str(text or "").split())


def make_entry_id(
    engine: str,
    file_path: str,
    location: Dict[str, Any],
    source_text: str,
) -> str:
    """
    Stable entry id.

    It depends on:
      - engine
      - relative file path
      - location inside file
      - normalized source text
    """
    location_json = json.dumps(
        location or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    payload = "|".join(
        [
            engine or "",
            file_path or "",
            location_json,
            normalize_text(source_text),
        ]
    )

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def make_source_hash(source_text: str, target_language: str = "") -> str:
    """
    Hash used by translation memory cache.
    """
    payload = normalize_text(source_text) + "||" + (target_language or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class TranslationEntry:
    id: str
    source_text: str
    target_text: Optional[str] = None

    context: str = ""
    file_path: str = ""
    engine: str = ""
    location: Dict[str, Any] = field(default_factory=dict)

    status: EntryStatus = EntryStatus.PENDING

    locked: bool = False
    ignored: bool = False
    fuzzy: bool = False

    machine_translated: bool = False
    human_reviewed: bool = False

    note: str = ""

    @property
    def source_hash(self) -> str:
        return make_source_hash(self.source_text)