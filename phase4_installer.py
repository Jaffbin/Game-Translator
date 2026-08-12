from __future__ import annotations

import argparse
import sys

from agl.installer import (
    install_patch,
    list_backups,
    rollback,
)


def cmd_install(args: argparse.Namespace) -> None:
    try:
        backup_id, installed = install_patch(
            project_dir=args.project_dir,
            patch_dir=args.patch,
            game_root=args.game_path,
            force=args.force,
            backup=not args.no_backup,
        )
    except Exception as exc:
        sys.exit(str(exc))

    print("Patch installation complete.")
    print(f"Backup ID: {backup_id}")
    print(f"Installed {len(installed)} file(s):")

    for file_path in installed:
        print(f"  {file_path}")


def cmd_backups(args: argparse.Namespace) -> None:
    backups = list_backups(args.project_dir)

    if not backups:
        print("No backups found.")
        return

    print(f"Found {len(backups)} backup(s):")

    for backup in backups:
        backup_id = backup.get("backup_id", "?")
        created_at = backup.get("created_at", "?")
        file_count = len(backup.get("files", []))

        print(f"  {backup_id}  {created_at}  files={file_count}")


def cmd_rollback(args: argparse.Namespace) -> None:
    try:
        backup_id, restored, deleted = rollback(
            project_dir=args.project_dir,
            backup_id=args.backup_id,
            game_root=args.game_path,
            force=args.force,
        )
    except Exception as exc:
        sys.exit(str(exc))

    print("Rollback complete.")
    print(f"Backup ID: {backup_id}")
    print(f"Restored {len(restored)} file(s):")

    for file_path in restored:
        print(f"  {file_path}")

    if deleted:
        print(f"Deleted {len(deleted)} file(s) added by patch:")
        for file_path in deleted:
            print(f"  {file_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 4: patch install / backup / rollback"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = subparsers.add_parser(
        "install",
        help="Install patch into game directory",
    )
    p_install.add_argument("project_dir")
    p_install.add_argument("--patch", required=True)
    p_install.add_argument("--game-path", default=None)
    p_install.add_argument("--force", action="store_true")
    p_install.add_argument("--no-backup", action="store_true")
    p_install.set_defaults(func=cmd_install)

    # backups
    p_backups = subparsers.add_parser(
        "backups",
        help="List backups",
    )
    p_backups.add_argument("project_dir")
    p_backups.set_defaults(func=cmd_backups)

    # rollback
    p_rollback = subparsers.add_parser(
        "rollback",
        help="Rollback game files from backup",
    )
    p_rollback.add_argument("project_dir")
    p_rollback.add_argument("--backup-id", default=None)
    p_rollback.add_argument("--game-path", default=None)
    p_rollback.add_argument("--force", action="store_true")
    p_rollback.set_defaults(func=cmd_rollback)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()