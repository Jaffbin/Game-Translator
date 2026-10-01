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

TRANSLATE_BLOCK_RE = re.compile(
    r'^(?P<indent>\s*)translate\s+[A-Za-z_]\w*\s+'
    r'(?P<label>[A-Za-z_]\w*)\s*:\s*(?:#.*)?$'
)

# Generated Ren'Py translation files keep the source dialogue as a comment and
# put the translated dialogue directly below it.  Limit the prefix to Ren'Py
# identifiers so Python expressions and other executable statements are never
# treated as editable text.
DIALOGUE_LINE_RE = re.compile(
    r'^(?P<indent>\s*)'
    r'(?:(?P<prefix>[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)\s+)?'
    r'"(?P<text>(?:\\.|[^"\\])*)"'
    r'(?P<suffix>\s*(?:\([^#\r\n]*\))?\s*(?:#.*)?)$'
)

COMMENT_RE = re.compile(r'^(?P<indent>\s*)#\s?(?P<body>.*)$')


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

    Ren'Py extraction strategy:
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
            raise

        lines = text.splitlines()

        entries: List[TranslationEntry] = []
        seen: Set[str] = set()

        pending_old: Optional[Tuple[int, str, str]] = None
        translate_indent: Optional[int] = None
        pending_dialogue: Optional[Tuple[int, str, str]] = None

        for line_number, line in enumerate(lines, start=1):
            block_match = TRANSLATE_BLOCK_RE.match(line)
            if block_match:
                # The special strings block is handled by old/new parsing.
                translate_indent = (
                    None
                    if block_match.group("label") == "strings"
                    else len(block_match.group("indent"))
                )
                pending_dialogue = None
            elif (
                translate_indent is not None
                and line.strip()
                and len(line) - len(line.lstrip()) <= translate_indent
            ):
                translate_indent = None
                pending_dialogue = None

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

            if pending_old is not None and line.strip() and not line.lstrip().startswith("#"):
                # Only comments and blank lines may separate an old/new pair.
                pending_old = None

            if translate_indent is None:
                continue

            comment_match = COMMENT_RE.match(line)
            if comment_match:
                source_match = DIALOGUE_LINE_RE.match(comment_match.group("body"))
                if source_match:
                    source_text = _unescape_renpy(source_match.group("text"))
                    if source_text.strip():
                        pending_dialogue = (
                            line_number,
                            source_match.group("prefix") or "",
                            source_text,
                        )
                continue

            if not line.strip():
                continue

            target_match = DIALOGUE_LINE_RE.match(line)
            if target_match and pending_dialogue is not None:
                source_line, source_prefix, source_text = pending_dialogue
                target_prefix = target_match.group("prefix") or ""

                # A matching speaker/expression prefix is a strong signal that
                # this is the generated target for the commented source line.
                if source_prefix == target_prefix:
                    target_text = _unescape_renpy(target_match.group("text"))
                    if not target_text.strip():
                        target_text = None

                    location = {
                        "type": "renpy_dialogue_pair",
                        "source_line": source_line,
                        "target_line": line_number,
                    }
                    entry_id = make_entry_id(
                        engine=self.engine_id,
                        file_path=rel_path,
                        location=location,
                        source_text=source_text,
                    )

                    if entry_id not in seen:
                        seen.add(entry_id)
                        entries.append(
                            TranslationEntry(
                                id=entry_id,
                                source_text=source_text,
                                target_text=target_text,
                                context=f"{rel_path}:{source_line}->{line_number}",
                                file_path=rel_path,
                                engine=self.engine_id,
                                location=location,
                                status=(
                                    EntryStatus.REVIEWED
                                    if target_text is not None
                                    else EntryStatus.PENDING
                                ),
                                human_reviewed=target_text is not None,
                            )
                        )

                pending_dialogue = None
                continue

            # Any executable statement breaks the source/target pairing.
            pending_dialogue = None

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
            original_bytes = source_path.read_bytes()
            has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
            original_text = original_bytes.decode("utf-8-sig")
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

            location_type = location.get("type")
            if location_type not in {"renpy_old_new", "renpy_dialogue_pair"}:
                continue

            target_line_number = (
                location.get("new_line")
                if location_type == "renpy_old_new"
                else location.get("target_line")
            )

            if not isinstance(target_line_number, int):
                continue

            if target_line_number < 1 or target_line_number > len(lines):
                print(
                    f"[WARN] Invalid target line {target_line_number} "
                    f"for entry {entry.id}"
                )
                continue

            original_line = lines[target_line_number - 1]

            # Separate line ending so regex matching is easier.
            ending = ""
            content = original_line

            if content.endswith("\n"):
                ending = "\n"
                content = content[:-1]

            if content.endswith("\r"):
                ending = "\r" + ending
                content = content[:-1]

            target_match = (
                NEW_LINE_RE.match(content)
                if location_type == "renpy_old_new"
                else DIALOGUE_LINE_RE.match(content)
            )

            if not target_match:
                print(
                    f"[WARN] Line {target_line_number} is not a valid "
                    f"Ren'Py translation target: {content!r}"
                )
                continue

            escaped_target = _escape_renpy(entry.target_text)
            if location_type == "renpy_old_new":
                replacement = (
                    f'{target_match.group(1)}"{escaped_target}"'
                    f'{target_match.group(3)}{ending}'
                )
            else:
                prefix = target_match.group("prefix")
                replacement = target_match.group("indent")
                if prefix:
                    replacement += f"{prefix} "
                replacement += (
                    f'"{escaped_target}"{target_match.group("suffix")}{ending}'
                )

            lines[target_line_number - 1] = replacement

            changed += 1

        target_path.parent.mkdir(parents=True, exist_ok=True)

        encoded = "".join(lines).encode("utf-8")
        if has_bom:
            encoded = b"\xef\xbb\xbf" + encoded
        # Write bytes so Windows text-mode newline conversion cannot turn
        # preserved CRLF endings into CRCRLF.
        target_path.write_bytes(encoded)

        return changed
