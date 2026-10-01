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

        system = {
            "gameTitle": "Sample Game",
            "currencyUnit": "Gold",
            "armorTypes": ["", "Light Armor"],
            "elements": ["", "Fire"],
            "equipTypes": ["", "Weapon"],
            "skillTypes": ["", "Magic"],
            "weaponTypes": ["", "Sword"],
            "switches": ["", "Developer Door"],
            "variables": ["", "Quest State"],
            "terms": {
                "basic": ["Level"],
                "commands": ["Fight"],
                "params": ["Max HP"],
                "messages": {"alwaysDash": "Always Dash"},
            },
        }

        (data / "System.json").write_text(
            json.dumps(system),
            encoding="utf-8",
        )

        plugins = [
            {
                "name": "ShopScene_Extension",
                "status": True,
                "description": "",
                "parameters": {"NoneItemText": "Nothing equipped"},
            },
            {
                "name": "AnotherNewGame",
                "status": True,
                "description": "",
                "parameters": {
                    "anotherDataList": json.dumps(
                        [json.dumps({"name": "Extra Story", "mapId": "1"})]
                    )
                },
            },
        ]
        js = root / "js"
        js.mkdir()
        (js / "plugins.js").write_text(
            "var $plugins =\n" + json.dumps(plugins) + ";\n",
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
                                {
                                    "code": 101,
                                    "indent": 0,
                                    "parameters": ["Actor1", 0, 0, 2, "Guide"],
                                },
                                {
                                    "code": 405,
                                    "indent": 0,
                                    "parameters": ["Long ago..."],
                                },
                                {
                                    "code": 320,
                                    "indent": 0,
                                    "parameters": [1, "New Name"],
                                },
                                {
                                    "code": 357,
                                    "indent": 0,
                                    "parameters": [
                                        "TextPicture",
                                        "set",
                                        "Set Text",
                                        {"text": "Chapter One"},
                                    ],
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
            self.assertTrue(any("plugins.js" in f for f in files))

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
            self.assertIn("Sample Game", sources)
            self.assertIn("Gold", sources)
            self.assertIn("Always Dash", sources)
            self.assertIn("Guide", sources)
            self.assertIn("Long ago...", sources)
            self.assertIn("New Name", sources)
            self.assertIn("Chapter One", sources)
            self.assertIn("Nothing equipped", sources)
            self.assertIn("Extra Story", sources)

            # Developer-only labels and Show Text asset names are not player
            # text and must remain untouched.
            self.assertNotIn("Developer Door", sources)
            self.assertNotIn("Quest State", sources)
            self.assertNotIn("Actor1", sources)

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
            self.assertEqual(command_list[2]["parameters"][4], "ZH:Guide")
            self.assertEqual(command_list[3]["parameters"][0], "ZH:Long ago...")
            self.assertEqual(command_list[4]["parameters"][1], "ZH:New Name")
            self.assertEqual(command_list[5]["parameters"][3]["text"], "ZH:Chapter One")

            plugins_file = root / "js" / "plugins.js"
            plugins_out = root / "patch" / "js" / "plugins.js"
            plugin_entries = [
                entry for entry in entries if entry.file_path.endswith("plugins.js")
            ]
            handler.inject_file(
                file_path=str(plugins_file),
                entries=plugin_entries,
                output_path=str(plugins_out),
            )
            rendered = plugins_out.read_text(encoding="utf-8")
            patched_plugins = json.loads(
                rendered[rendered.find("[") : rendered.rfind("]") + 1]
            )
            self.assertEqual(
                patched_plugins[0]["parameters"]["NoneItemText"],
                "ZH:Nothing equipped",
            )
            nested = json.loads(
                patched_plugins[1]["parameters"]["anotherDataList"]
            )
            nested_item = json.loads(nested[0])
            self.assertEqual(nested_item["name"], "ZH:Extra Story")


if __name__ == "__main__":
    unittest.main()
