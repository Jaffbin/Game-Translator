from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .project import ProjectStore


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _new_backup_id() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()

    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------

def load_patch_manifest(patch_dir: Path | str) -> Dict[str, Any]:
    patch_dir = Path(patch_dir)
    manifest_path = patch_dir / "manifest.json"

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Patch manifest not found: {manifest_path}"
        )

    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_patch(
    patch_dir: Path | str,
    game_root: Path | str,
    manifest: Dict[str, Any],
    force: bool = False,
) -> List[str]:
    """
    Verify patch files and original game files.

    Checks:
      - patched file exists
      - patched sha256 matches manifest
      - original file exists when manifest expects it
      - original sha256 matches manifest
    """
    patch_dir = Path(patch_dir)
    game_root = Path(game_root)

    issues: List[str] = []

    for item in manifest.get("files", []):
        original_rel = item.get("original_path") or item.get("patched_path")
        patch_rel = item.get("patched_path") or item.get("original_path")

        if not original_rel or not patch_rel:
            issues.append("Manifest item missing original_path/patched_path.")
            continue

        patched_file = patch_dir / patch_rel
        original_file = game_root / original_rel

        if not patched_file.exists():
            issues.append(f"Missing patched file: {patch_rel}")
            continue

        expected_patched_sha = item.get("patched_sha256")
        if expected_patched_sha:
            actual_patched_sha = sha256_file(patched_file)
            if actual_patched_sha != expected_patched_sha:
                issues.append(
                    f"Patched file hash mismatch: {patch_rel}"
                )

        expected_original_sha = item.get("original_sha256")
        if expected_original_sha:
            if not original_file.exists():
                issues.append(
                    f"Original file missing but expected: {original_rel}"
                )
            else:
                actual_original_sha = sha256_file(original_file)
                if actual_original_sha != expected_original_sha:
                    issues.append(
                        f"Original file hash mismatch: {original_rel}. "
                        "Game files may have changed since patch creation."
                    )

    if issues and not force:
        raise RuntimeError(
            "Patch verification failed:\n  - " + "\n  - ".join(issues)
        )

    return issues


# ----------------------------------------------------------------------
# Backup
# ----------------------------------------------------------------------

def create_backup(
    project_dir: Path | str,
    game_root: Path | str,
    manifest: Dict[str, Any],
    patch_dir: Optional[Path | str] = None,
) -> Tuple[str, Path]:
    """
    Backup original files before installing patch.

    Backup structure:

      project_dir/
        backups/
          20260806_123456_789/
            manifest.json
            data/
              Items.json
    """
    project_dir = Path(project_dir)
    game_root = Path(game_root)

    backup_id = _new_backup_id()
    backup_dir = project_dir / "backups" / backup_id
    backup_dir.mkdir(parents=True, exist_ok=True)

    files: List[Dict[str, Any]] = []

    for item in manifest.get("files", []):
        rel_path = item.get("original_path") or item.get("patched_path")

        if not rel_path:
            continue

        original_file = game_root / rel_path

        record = {
            "game_path": rel_path,
            "backup_path": rel_path,
            "existed_before": original_file.exists(),
        }

        if original_file.exists():
            backup_file = backup_dir / rel_path
            backup_file.parent.mkdir(parents=True, exist_ok=True)

            shutil.copy2(original_file, backup_file)

            record["original_sha256"] = sha256_file(original_file)

        files.append(record)

    backup_manifest = {
        "backup_id": backup_id,
        "created_at": _now_iso(),
        "game_path": str(game_root),
        "patch_path": str(patch_dir) if patch_dir else None,
        "files": files,
    }

    (backup_dir / "manifest.json").write_text(
        json.dumps(backup_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return backup_id, backup_dir


def list_backups(project_dir: Path | str) -> List[Dict[str, Any]]:
    project_dir = Path(project_dir)
    backups_dir = project_dir / "backups"

    if not backups_dir.exists():
        return []

    result: List[Dict[str, Any]] = []

    for backup_dir in sorted(backups_dir.iterdir(), reverse=True):
        if not backup_dir.is_dir():
            continue

        manifest_path = backup_dir / "manifest.json"

        if not manifest_path.exists():
            continue

        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            result.append(manifest)
        except Exception:
            continue

    return result


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------

def install_patch(
    project_dir: Path | str,
    patch_dir: Path | str,
    game_root: Optional[Path | str] = None,
    force: bool = False,
    backup: bool = True,
) -> Tuple[Optional[str], List[str]]:
    """
    Install patch into game directory.

    Returns:
      backup_id, installed_files
    """
    project_dir = Path(project_dir)
    patch_dir = Path(patch_dir)

    if game_root is None:
        with ProjectStore(project_dir) as store:
            game_root = Path(store.meta["game_path"])

    game_root = Path(game_root).resolve()

    manifest = load_patch_manifest(patch_dir)

    verify_patch(
        patch_dir=patch_dir,
        game_root=game_root,
        manifest=manifest,
        force=force,
    )

    backup_id: Optional[str] = None

    if backup:
        backup_id, _ = create_backup(
            project_dir=project_dir,
            game_root=game_root,
            manifest=manifest,
            patch_dir=patch_dir,
        )

    installed: List[str] = []

    for item in manifest.get("files", []):
        original_rel = item.get("original_path") or item.get("patched_path")
        patch_rel = item.get("patched_path") or item.get("original_path")

        if not original_rel or not patch_rel:
            continue

        source_file = patch_dir / patch_rel
        target_file = game_root / original_rel

        if not source_file.exists():
            continue

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)

        installed.append(original_rel)

    project_dir.mkdir(parents=True, exist_ok=True)

    last_install = {
        "installed_at": _now_iso(),
        "patch_dir": str(patch_dir),
        "game_root": str(game_root),
        "backup_id": backup_id,
        "files": installed,
    }

    (project_dir / "last_install.json").write_text(
        json.dumps(last_install, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return backup_id, installed


# ----------------------------------------------------------------------
# Rollback
# ----------------------------------------------------------------------

def rollback(
    project_dir: Path | str,
    backup_id: Optional[str] = None,
    game_root: Optional[Path | str] = None,
    force: bool = False,
) -> Tuple[str, List[str], List[str]]:
    """
    Rollback game files from backup.

    Returns:
      backup_id, restored_files, deleted_files
    """
    project_dir = Path(project_dir)

    if game_root is None:
        with ProjectStore(project_dir) as store:
            game_root = Path(store.meta["game_path"])

    game_root = Path(game_root).resolve()

    if backup_id is None:
        backups = list_backups(project_dir)
        if not backups:
            raise FileNotFoundError("No backups found.")

        backup_id = backups[0]["backup_id"]

    backup_dir = project_dir / "backups" / backup_id
    manifest_path = backup_dir / "manifest.json"

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Backup manifest not found: {manifest_path}"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    restored: List[str] = []
    deleted: List[str] = []

    for item in manifest.get("files", []):
        rel_path = item.get("game_path")

        if not rel_path:
            continue

        target_file = game_root / rel_path

        if item.get("existed_before"):
            backup_file = backup_dir / item.get("backup_path", rel_path)

            if not backup_file.exists():
                raise FileNotFoundError(
                    f"Backup file missing: {backup_file}"
                )

            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, target_file)

            restored.append(rel_path)
        else:
            # This file did not exist before patch installation.
            # Rollback should remove it.
            if target_file.exists():
                target_file.unlink()
                deleted.append(rel_path)

    return backup_id, restored, deleted