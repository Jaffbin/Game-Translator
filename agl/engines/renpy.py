from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import EngineHandler
from ..models import EntryStatus, TranslationEntry, make_entry_id


OLD_LINE_RE = re.compile(
    r'^(\s*old\s+)"((?:\\.|[^"\\])*)"(.*)$'
)

NEW_LINE_RE = re.compile(
    r'^(\s*new\s+)"((?:\\.|[^"\\])*)"(.*)$'
)


def _unescape_renpy(text: str) -> str:
    """
    Unescape common Ren'Py / Python-like string escapes.

    Handles:
      \\n
      \\t
      \\"
      \\'
      \\\\
    """
    out = []
    i = 0

    while i < len(text):
        ch = text[i]

        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]

            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == '"':
                out.append('"')
            elif nxt == "'":
                out.append("'")
            elif nxt == "\\":
                out.append("\\")
            else:
                # Keep unknown escapes as-is.
                out.append("\\")
                out.append(nxt)

            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _escape_renpy(text: str) -> str:
    """
    Escape text before writing it into a Ren'Py quoted string.
    """
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


class RenPyHandler(EngineHandler):
    """
    Ren'Py handler.

    Phase 3 strategy:
      - Prefer existing Ren'Py translation templates.
      - Extract old/new pairs.
      - Translate old text.
      - Write translation into new line.
      - Do not directly rewrite original game script if possible.

    Expected template example:

      translate chinese strings:
          old "Start"
          new ""

          old "Load"
          new ""
    """

    engine_id = "renpy"
    display_name = "Ren'Py"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, game_path: str) -> bool:
        game_dir = Path(game_path) / "game"

        if not game_dir.is_dir():
            return False

        if any(game_dir.glob("*.rpy")):
            return True

        if any(game_dir.glob("*.rpyc")):
            return True

        if (game_dir / "tl").is_dir():
            return True

        # Slightly more expensive fallback.
        if any(game_dir.rglob("*.rpy")):
            return True

        if any(game_dir.rglob("*.rpyc")):
            return True

        return False

    def _game_dir(self, game_path: str) -> Path:
        return Path(game_path) / "game"

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def find_text_files(self, game_path: str) -> List[str]:
        game_dir = self._game_dir(game_path)

        if not game_dir.is_dir():
            return []

        tl_dir = game_dir / "tl"

        # Preferred mode:
        # Use Ren'Py translation templates under game/tl.
        if tl_dir.is_dir():
            files = sorted(tl_dir.rglob("*.rpy"))
            if files:
                return [str(path) for path in files]

        # Fallback:
        # Scan all .rpy files. This may still only produce entries
        # if old/new translation template syntax exists.
        files = sorted(game_dir.rglob("*.rpy"))
        return [str(path) for path in files]

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

        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            print(f"[WARN] Failed to read Ren'Py file {rel_path}: {exc}")
            return []

        lines = text.splitlines()

        entries: List[TranslationEntry] = []
        seen: Set[str] = set()

        pending_old: Optional[Tuple[int, str, str]] = None

        for line_number, line in enumerate(lines, start=1):
            old_match = OLD_LINE_RE.match(line)

            if old_match:
                old_raw = old_match.group(2)
                source_text = _unescape_renpy(old_raw).strip()

                if source_text:
                    pending_old = (line_number, old_raw, source_text)
                else:
                    pending_old = None

                continue

            new_match = NEW_LINE_RE.match(line)

            if new_match and pending_old is not None:
                old_line_number, old_raw, source_text = pending_old
                new_raw = new_match.group(2)

                target_text = _unescape_renpy(new_raw)
                if not target_text.strip():
                    target_text = None

                location = {
                    "type": "renpy_old_new",
                    "old_line": old_line_number,
                    "new_line": line_number,
                }

                entry_id = make_entry_id(
                    engine=self.engine_id,
                    file_path=rel_path,
                    location=location,
                    source_text=source_text,
                )

                if entry_id in seen:
                    pending_old = None
                    continue

                seen.add(entry_id)

                if target_text is not None:
                    status = EntryStatus.REVIEWED
                    human_reviewed = True
                else:
                    status = EntryStatus.PENDING
                    human_reviewed = False

                entries.append(
                    TranslationEntry(
                        id=entry_id,
                        source_text=source_text,
                        target_text=target_text,
                        context=f"{rel_path}:{old_line_number}->{line_number}",
                        file_path=rel_path,
                        engine=self.engine_id,
                        location=location,
                        status=status,
                        human_reviewed=human_reviewed,
                    )
                )

                pending_old = None
                continue

            # We intentionally keep pending_old across blank lines/comments,
            # but another old statement will overwrite it.

        return entries

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

        try:
            original_text = source_path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            print(f"[WARN] Failed to read Ren'Py file {file_path}: {exc}")
            return 0

        lines = original_text.splitlines(keepends=True)
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

            if location.get("type") != "renpy_old_new":
                continue

            new_line_number = location.get("new_line")

            if not isinstance(new_line_number, int):
                continue

            if new_line_number < 1 or new_line_number > len(lines):
                print(
                    f"[WARN] Invalid new_line {new_line_number} "
                    f"for entry {entry.id}"
                )
                continue

            original_line = lines[new_line_number - 1]

            # Separate line ending so regex matching is easier.
            ending = ""
            content = original_line

            if content.endswith("\n"):
                ending = "\n"
                content = content[:-1]

            if content.endswith("\r"):
                ending = "\r" + ending
                content = content[:-1]

            new_match = NEW_LINE_RE.match(content)

            if not new_match:
                print(
                    f"[WARN] Line {new_line_number} is not a valid "
                    f"Ren'Py new statement: {content!r}"
                )
                continue

            escaped_target = _escape_renpy(entry.target_text)

            lines[new_line_number - 1] = (
                f'{new_match.group(1)}"{escaped_target}"{new_match.group(3)}{ending}'
            )

            changed += 1

        target_path.parent.mkdir(parents=True, exist_ok=True)

        target_path.write_text(
            "".join(lines),
            encoding="utf-8",
        )

        return changed