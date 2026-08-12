import json
import tempfile
import unittest
from pathlib import Path

from agl.engines.rpgmaker import RPGMakerHandler
from agl.models import EntryStatus


class TestRPGMakerPhase1(unittest.TestCase):
    def build_game(self, root: Path) -> None:
        data = root / "data"
        data.mkdir(parents=True)

        items = [
            None,
            {
                "id": 1,
                "name": "Potion",
                "description": "Restores HP",
            },
            {
                "id": 2,
                "name": "icon.png",
                "description": "",
            },
        ]

        (data / "Items.json").write_text(
            json.dumps(items),
            encoding="utf-8",
        )

        map_data = {
            "events": [
                None,
                {
                    "pages": [
                        {
                            "list": [
                                {
                                    "code": 401,
                                    "indent": 0,
                                    "parameters": ["Hello \\v[1]"],
                                },
                                {
                                    "code": 102,
                                    "indent": 0,
                                    "parameters": [["Yes", "No"]],
                                },
                            ]
                        }
                    ]
                },
            ]
        }

        (data / "Map001.json").write_text(
            json.dumps(map_data),
            encoding="utf-8",
        )

    def test_extract_and_inject(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_game(root)

            handler = RPGMakerHandler()

            self.assertTrue(handler.detect(str(root)))

            files = handler.find_text_files(str(root))

            self.assertTrue(any("Items.json" in f for f in files))
            self.assertTrue(any("Map001.json" in f for f in files))

            entries = []

            for file_path in files:
                entries.extend(
                    handler.extract_file(file_path, str(root))
                )

            sources = {entry.source_text for entry in entries}

            self.assertIn("Potion", sources)
            self.assertIn("Restores HP", sources)
            self.assertIn("Hello \\v[1]", sources)
            self.assertIn("Yes", sources)
            self.assertIn("No", sources)

            # File-like string should be skipped.
            self.assertNotIn("icon.png", sources)

            for entry in entries:
                entry.target_text = f"ZH:{entry.source_text}"
                entry.status = EntryStatus.MACHINE_TRANSLATED

            items_file = root / "data" / "Items.json"
            items_out = root / "patch" / "data" / "Items.json"

            items_entries = [
                entry
                for entry in entries
                if entry.file_path.endswith("Items.json")
            ]

            handler.inject_file(
                file_path=str(items_file),
                entries=items_entries,
                output_path=str(items_out),
            )

            patched_items = json.loads(
                items_out.read_text(encoding="utf-8")
            )

            self.assertEqual(patched_items[1]["name"], "ZH:Potion")
            self.assertEqual(
                patched_items[1]["description"],
                "ZH:Restores HP",
            )

            map_file = root / "data" / "Map001.json"
            map_out = root / "patch" / "data" / "Map001.json"

            map_entries = [
                entry
                for entry in entries
                if entry.file_path.endswith("Map001.json")
            ]

            handler.inject_file(
                file_path=str(map_file),
                entries=map_entries,
                output_path=str(map_out),
            )

            patched_map = json.loads(
                map_out.read_text(encoding="utf-8")
            )

            command_list = patched_map["events"][1]["pages"][0]["list"]

            self.assertEqual(
                command_list[0]["parameters"][0],
                "ZH:Hello \\v[1]",
            )

            self.assertEqual(
                command_list[1]["parameters"][0][0],
                "ZH:Yes",
            )

            self.assertEqual(
                command_list[1]["parameters"][0][1],
                "ZH:No",
            )


if __name__ == "__main__":
    unittest.main()