import tempfile
import unittest
from pathlib import Path

from agl.engines.renpy import RenPyHandler
from agl.models import EntryStatus
from agl.project import ProjectStore
from agl.services import delivery, scan


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

    def test_generated_dialogue_pairs_extract_and_inject(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            tl_dir = root / "game" / "tl" / "schinese"
            tl_dir.mkdir(parents=True)
            source_file = tl_dir / "dialogue.rpy"
            source_file.write_text(
                '''translate schinese start_abcd:

    # e happy "Hello [player]."
    e happy ""

    # "A narrated line."
    "已有译文"
''',
                encoding="utf-8",
            )

            handler = RenPyHandler()
            entries = handler.extract_file(str(source_file), str(root))

            self.assertEqual(len(entries), 2)
            by_source = {entry.source_text: entry for entry in entries}
            greeting = by_source["Hello [player]."]
            narration = by_source["A narrated line."]
            self.assertIsNone(greeting.target_text)
            self.assertEqual(greeting.location["type"], "renpy_dialogue_pair")
            self.assertEqual(narration.target_text, "已有译文")
            self.assertEqual(narration.status, EntryStatus.REVIEWED)

            greeting.target_text = "你好，[player]。"
            greeting.status = EntryStatus.MACHINE_TRANSLATED
            output_file = root / "patch" / "dialogue.rpy"
            changed = handler.inject_file(
                str(source_file), entries, str(output_file)
            )

            self.assertEqual(changed, 2)
            patched = output_file.read_text(encoding="utf-8")
            self.assertIn('e happy "你好，[player]。"', patched)
            self.assertIn('"已有译文"', patched)

    def test_unrelated_statement_breaks_old_new_pair(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_file = root / "game" / "tl" / "chinese" / "broken.rpy"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                '''translate chinese strings:
    old "Start"
    $ unexpected = True
    new ""
''',
                encoding="utf-8",
            )

            entries = RenPyHandler().extract_file(str(source_file), str(root))
            self.assertEqual(entries, [])

    def test_dialogue_patch_install_and_rollback_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game = root / "RenPyGame"
            source_file = game / "game" / "tl" / "schinese" / "script.rpy"
            source_file.parent.mkdir(parents=True)
            original = '''translate schinese start_abcd:
    # e "Hello [player]."
    e ""

translate schinese strings:
    old "Start"
    new ""
'''
            source_file.write_text(original, encoding="utf-8")

            project_dir = root / "Project"
            with ProjectStore.create(
                project_dir,
                game,
                engine_id="renpy",
                target_language="zh-CN",
            ):
                pass

            result = scan.scan_project(project_dir)
            self.assertEqual(result["entries"], 2)

            with ProjectStore(project_dir) as store:
                by_source = {entry.source_text: entry for entry in store.all_entries()}
                store.update_entry(
                    by_source["Hello [player]."].id,
                    target_text="你好，[player]。",
                    status=EntryStatus.REVIEWED,
                    human_reviewed=True,
                )
                store.update_entry(
                    by_source["Start"].id,
                    target_text="开始",
                    status=EntryStatus.REVIEWED,
                    human_reviewed=True,
                )

            patch = delivery.patch_project(project_dir, patch_name="renpy-test")
            patched = Path(patch["patch_dir"]) / source_file.relative_to(game)
            self.assertIn('e "你好，[player]。"', patched.read_text(encoding="utf-8"))

            installed = delivery.install_project(project_dir, patch["patch_dir"])
            self.assertIn('new "开始"', source_file.read_text(encoding="utf-8"))
            delivery.rollback_project(project_dir, installed["backup_id"])
            self.assertEqual(source_file.read_text(encoding="utf-8"), original)

    def test_injection_preserves_utf8_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_file = root / "game" / "tl" / "chinese" / "bom.rpy"
            source_file.parent.mkdir(parents=True)
            source_file.write_bytes(
                b"\xef\xbb\xbftranslate chinese strings:\r\n"
                b"    old \"Start\"\r\n"
                b"    new \"\"\r\n"
            )

            handler = RenPyHandler()
            entries = handler.extract_file(str(source_file), str(root))
            entries[0].target_text = "Begin"
            entries[0].status = EntryStatus.REVIEWED
            output_file = root / "patch" / "bom.rpy"
            handler.inject_file(str(source_file), entries, str(output_file))

            patched = output_file.read_bytes()
            self.assertTrue(patched.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b'new "Begin"\r\n', patched)

if __name__ == "__main__":
    unittest.main()
