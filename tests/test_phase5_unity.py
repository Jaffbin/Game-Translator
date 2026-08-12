import json
import tempfile
import unittest
from pathlib import Path

from agl.engines.unity import UnityHandler
from agl.models import EntryStatus


class TestUnityPhase5(unittest.TestCase):
    def build_game(self, root: Path) -> None:
        streaming = root / "StreamingAssets"
        streaming.mkdir(parents=True)

        # JSON
        json_data = {
            "ui": {
                "title": "Start Game",
                "id": "abc123",
                "icon": "icon_start.png",
            },
            "dialogs": [
                {
                    "text": "Hello, world!"
                },
                {
                    "text": "filename.txt"
                },
            ],
        }

        (streaming / "text.json").write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # TXT
        txt_content = """# Comment line
Welcome to the game.
AssetPath: file.png
"""

        (streaming / "lines.txt").write_text(
            txt_content,
            encoding="utf-8",
        )

        # CSV
        csv_content = """id,text
1,Hello there
2,icon.png
"""

        (streaming / "table.csv").write_text(
            csv_content,
            encoding="utf-8",
        )

    def test_detect(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_game(root)

            handler = UnityHandler()
            self.assertTrue(handler.detect(str(root)))

    def test_extract_and_inject(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_game(root)

            handler = UnityHandler()

            files = handler.find_text_files(str(root))

            self.assertTrue(
                any("text.json" in f for f in files)
            )
            self.assertTrue(
                any("lines.txt" in f for f in files)
            )
            self.assertTrue(
                any("table.csv" in f for f in files)
            )

            entries = []

            for file_path in files:
                entries.extend(
                    handler.extract_file(file_path, str(root))
                )

            sources = {entry.source_text for entry in entries}

            # JSON
            self.assertIn("Start Game", sources)
            self.assertIn("Hello, world!", sources)

            # TXT
            self.assertIn("Welcome to the game.", sources)

            # CSV
            self.assertIn("Hello there", sources)

            # Should not extract technical values.
            self.assertNotIn("abc123", sources)
            self.assertNotIn("icon_start.png", sources)
            self.assertNotIn("filename.txt", sources)
            self.assertNotIn("icon.png", sources)

            # Simulate translation.
            for entry in entries:
                if entry.source_text == "Start Game":
                    entry.target_text = "开始游戏"
                    entry.status = EntryStatus.MACHINE_TRANSLATED

                elif entry.source_text == "Hello, world!":
                    entry.target_text = "你好，世界！"
                    entry.status = EntryStatus.MACHINE_TRANSLATED

                elif entry.source_text == "Welcome to the game.":
                    entry.target_text = "欢迎来到游戏。"
                    entry.status = EntryStatus.MACHINE_TRANSLATED

                elif entry.source_text == "Hello there":
                    entry.target_text = "你好，世界"
                    entry.status = EntryStatus.MACHINE_TRANSLATED

            patch_root = root / "Patch"

            # Inject JSON
            json_file = root / "StreamingAssets" / "text.json"
            json_out = patch_root / "StreamingAssets" / "text.json"

            json_entries = [
                entry
                for entry in entries
                if entry.file_path.endswith("text.json")
            ]

            handler.inject_file(
                file_path=str(json_file),
                entries=json_entries,
                output_path=str(json_out),
            )

            patched_json = json.loads(
                json_out.read_text(encoding="utf-8")
            )

            self.assertEqual(
                patched_json["ui"]["title"],
                "开始游戏",
            )

            self.assertEqual(
                patched_json["dialogs"][0]["text"],
                "你好，世界！",
            )

            # Should remain untouched.
            self.assertEqual(
                patched_json["dialogs"][1]["text"],
                "filename.txt",
            )

            # Inject TXT
            txt_file = root / "StreamingAssets" / "lines.txt"
            txt_out = patch_root / "StreamingAssets" / "lines.txt"

            txt_entries = [
                entry
                for entry in entries
                if entry.file_path.endswith("lines.txt")
            ]

            handler.inject_file(
                file_path=str(txt_file),
                entries=txt_entries,
                output_path=str(txt_out),
            )

            patched_txt = txt_out.read_text(encoding="utf-8")

            self.assertIn("欢迎来到游戏。", patched_txt)
            self.assertIn("# Comment line", patched_txt)

            # Inject CSV
            csv_file = root / "StreamingAssets" / "table.csv"
            csv_out = patch_root / "StreamingAssets" / "table.csv"

            csv_entries = [
                entry
                for entry in entries
                if entry.file_path.endswith("table.csv")
            ]

            handler.inject_file(
                file_path=str(csv_file),
                entries=csv_entries,
                output_path=str(csv_out),
            )

            patched_csv = csv_out.read_text(encoding="utf-8")

            self.assertIn("你好，世界", patched_csv)
            self.assertIn("icon.png", patched_csv)


if __name__ == "__main__":
    unittest.main()