import tempfile
import unittest
from pathlib import Path

from agl.engines.renpy import RenPyHandler
from agl.models import EntryStatus


class TestRenPyPhase3(unittest.TestCase):
    def build_game(self, root: Path) -> None:
        tl_dir = root / "game" / "tl" / "chinese"
        tl_dir.mkdir(parents=True)

        content = """translate chinese strings:

    old "Start"
    new ""

    old "Load"
    new "Load"

    old "Exit"
    new ""
"""

        (tl_dir / "script.rpy").write_text(
            content,
            encoding="utf-8",
        )

    def test_detect(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_game(root)

            handler = RenPyHandler()
            self.assertTrue(handler.detect(str(root)))

    def test_extract_and_inject(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_game(root)

            handler = RenPyHandler()

            files = handler.find_text_files(str(root))
            self.assertEqual(len(files), 1)

            entries = handler.extract_file(files[0], str(root))

            by_source = {entry.source_text: entry for entry in entries}

            self.assertIn("Start", by_source)
            self.assertIn("Load", by_source)
            self.assertIn("Exit", by_source)

            self.assertIsNone(by_source["Start"].target_text)
            self.assertEqual(by_source["Load"].target_text, "Load")
            self.assertIsNone(by_source["Exit"].target_text)

            # Simulate translation.
            by_source["Start"].target_text = "开始"
            by_source["Start"].status = EntryStatus.MACHINE_TRANSLATED

            by_source["Exit"].target_text = "退出"
            by_source["Exit"].status = EntryStatus.MACHINE_TRANSLATED

            output_file = root / "patch" / "script.rpy"

            changed = handler.inject_file(
                file_path=files[0],
                entries=entries,
                output_path=str(output_file),
            )

            self.assertGreaterEqual(changed, 2)

            patched = output_file.read_text(encoding="utf-8")

            self.assertIn('new "开始"', patched)
            self.assertIn('new "Load"', patched)
            self.assertIn('new "退出"', patched)

    def test_escape_quotes_and_newlines(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            tl_dir = root / "game" / "tl" / "chinese"
            tl_dir.mkdir(parents=True)

            content = """translate chinese strings:

    old "Say \\"hello\\""
    new ""
"""

            source_file = tl_dir / "escape.rpy"
            source_file.write_text(content, encoding="utf-8")

            handler = RenPyHandler()

            entries = handler.extract_file(str(source_file), str(root))

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].source_text, 'Say "hello"')

            # Use ASCII text to avoid editor/input method changing punctuation.
            entries[0].target_text = 'say:"hello"\nline2'
            entries[0].status = EntryStatus.MACHINE_TRANSLATED

            output_file = root / "patch" / "escape.rpy"

            handler.inject_file(
                file_path=str(source_file),
                entries=entries,
                output_path=str(output_file),
            )

            patched = output_file.read_text(encoding="utf-8")

            self.assertIn(
                'new "say:\\"hello\\"\\nline2"',
                patched,
            )

if __name__ == "__main__":
    unittest.main()