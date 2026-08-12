import json
import tempfile
import unittest
from pathlib import Path

from agl.installer import (
    install_patch,
    list_backups,
    rollback,
    sha256_file,
)


class TestPhase4Installer(unittest.TestCase):
    def build_game_and_patch(self, root: Path):
        game = root / "FakeGame"
        data = game / "data"
        data.mkdir(parents=True)

        original_items = data / "Items.json"
        original_items.write_text(
            '{"name":"Potion"}',
            encoding="utf-8",
        )

        patch = root / "Patch"
        patch_data = patch / "data"
        patch_data.mkdir(parents=True)

        patched_items = patch_data / "Items.json"
        patched_items.write_text(
            '{"name":"药水"}',
            encoding="utf-8",
        )

        new_file = patch_data / "New.json"
        new_file.write_text(
            '{"added_by_patch":true}',
            encoding="utf-8",
        )

        manifest = {
            "tool": "AutoGame Localizer",
            "version": "0.4.0-test",
            "files": [
                {
                    "original_path": "data/Items.json",
                    "patched_path": "data/Items.json",
                    "original_sha256": sha256_file(original_items),
                    "patched_sha256": sha256_file(patched_items),
                },
                {
                    "original_path": "data/New.json",
                    "patched_path": "data/New.json",
                    "original_sha256": None,
                    "patched_sha256": sha256_file(new_file),
                },
            ],
        }

        (patch / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return game, patch

    def test_install_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            game, patch = self.build_game_and_patch(root)

            project = root / "Project"
            project.mkdir(parents=True)

            # Install patch.
            backup_id, installed = install_patch(
                project_dir=project,
                patch_dir=patch,
                game_root=game,
            )

            self.assertIn("data/Items.json", installed)
            self.assertIn("data/New.json", installed)

            installed_items_text = (
                game / "data" / "Items.json"
            ).read_text(encoding="utf-8")

            self.assertIn("药水", installed_items_text)

            self.assertTrue((game / "data" / "New.json").exists())

            # Backup should exist.
            backups = list_backups(project)
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0]["backup_id"], backup_id)

            # Rollback.
            used_backup_id, restored, deleted = rollback(
                project_dir=project,
                backup_id=backup_id,
                game_root=game,
            )

            self.assertEqual(used_backup_id, backup_id)
            self.assertIn("data/Items.json", restored)
            self.assertIn("data/New.json", deleted)

            restored_items_text = (
                game / "data" / "Items.json"
            ).read_text(encoding="utf-8")

            self.assertIn("Potion", restored_items_text)

            # File added by patch should be removed after rollback.
            self.assertFalse((game / "data" / "New.json").exists())


if __name__ == "__main__":
    unittest.main()