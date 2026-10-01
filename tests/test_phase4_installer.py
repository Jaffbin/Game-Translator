import json
import tempfile
import unittest
from unittest.mock import patch as mock_patch
import shutil
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

    def test_partial_install_is_automatically_rolled_back(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game, patch = self.build_game_and_patch(root)
            project = root / "Project"
            original_copy2 = shutil.copy2

            def fail_second_patch_copy(source, target, *args, **kwargs):
                source_path = Path(source)
                if source_path == patch / "data" / "New.json":
                    raise OSError("simulated disk failure")
                return original_copy2(source, target, *args, **kwargs)

            with mock_patch("agl.installer.shutil.copy2", side_effect=fail_second_patch_copy):
                with self.assertRaisesRegex(RuntimeError, "original files were restored"):
                    install_patch(
                        project_dir=project,
                        patch_dir=patch,
                        game_root=game,
                    )

            self.assertEqual(
                (game / "data" / "Items.json").read_text(encoding="utf-8"),
                '{"name":"Potion"}',
            )
            self.assertFalse((game / "data" / "New.json").exists())
            self.assertFalse((project / "last_install.json").exists())

    def test_force_cannot_install_tampered_patch_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game, patch = self.build_game_and_patch(root)
            (patch / "data" / "Items.json").write_text(
                '{"name":"tampered"}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "Patched file hash mismatch"):
                install_patch(
                    project_dir=root / "Project",
                    patch_dir=patch,
                    game_root=game,
                    force=True,
                )

            self.assertEqual(
                (game / "data" / "Items.json").read_text(encoding="utf-8"),
                '{"name":"Potion"}',
            )

    def test_install_stops_when_new_patch_target_now_exists(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game, patch = self.build_game_and_patch(root)
            new_target = game / "data" / "New.json"
            new_target.write_text('{"created_by":"game update"}', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "expected a new file"):
                install_patch(
                    project_dir=root / "Project",
                    patch_dir=patch,
                    game_root=game,
                )

            self.assertEqual(
                new_target.read_text(encoding="utf-8"),
                '{"created_by":"game update"}',
            )

    def test_rollback_preserves_files_changed_after_install_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game, patch = self.build_game_and_patch(root)
            project = root / "Project"
            backup_id, _ = install_patch(project, patch, game_root=game)
            items = game / "data" / "Items.json"
            items.write_text('{"name":"changed after install"}', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "installed file changed"):
                rollback(project, backup_id=backup_id, game_root=game)

            self.assertIn("changed after install", items.read_text(encoding="utf-8"))
            rollback(project, backup_id=backup_id, game_root=game, force=True)
            self.assertEqual(items.read_text(encoding="utf-8"), '{"name":"Potion"}')

    def test_install_rejects_manifest_path_outside_game(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game, patch = self.build_game_and_patch(root)
            outside = root / "outside.json"
            outside.write_text('{"protected":true}', encoding="utf-8")
            manifest_path = patch / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["original_path"] = "../outside.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unsafe game file path"):
                install_patch(
                    project_dir=root / "Project",
                    patch_dir=patch,
                    game_root=game,
                )

            self.assertEqual(
                outside.read_text(encoding="utf-8"),
                '{"protected":true}',
            )

    def test_rollback_rejects_manifest_path_outside_game(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            game = root / "Game"
            game.mkdir()
            project = root / "Project"
            backup_id = "20260929_120000_1"
            backup = project / "backups" / backup_id
            backup.mkdir(parents=True)
            outside = root / "outside.json"
            outside.write_text('{"protected":true}', encoding="utf-8")
            (backup / "manifest.json").write_text(
                json.dumps(
                    {
                        "backup_id": backup_id,
                        "files": [
                            {
                                "game_path": "../outside.json",
                                "backup_path": "../outside.json",
                                "existed_before": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsafe game file path"):
                rollback(project, backup_id=backup_id, game_root=game)

            self.assertEqual(
                outside.read_text(encoding="utf-8"),
                '{"protected":true}',
            )


if __name__ == "__main__":
    unittest.main()
