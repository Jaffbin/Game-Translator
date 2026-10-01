from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .project import ProjectStore
from .io_utils import atomic_write_text


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


def _resolve_inside(root: Path | str, relative_path: str, label: str) -> Path:
    """Resolve a manifest path and require it to stay below ``root``.

    Patch and backup manifests are data files and may have been created by an
    older version or another tool. Treat every path in them as untrusted even
    when the surrounding project directory is local.
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError(f"Invalid {label} path in manifest.")

    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError(f"Unsafe absolute {label} path: {relative_path}")

    resolved_root = Path(root).resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Unsafe {label} path outside the allowed directory: {relative_path}"
        ) from exc
    if resolved == resolved_root:
        raise ValueError(f"Invalid {label} file path: {relative_path}")
    return resolved


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

    integrity_issues: List[str] = []
    original_issues: List[str] = []

    for item in manifest.get("files", []):
        original_rel = item.get("original_path") or item.get("patched_path")
        patch_rel = item.get("patched_path") or item.get("original_path")

        if not original_rel or not patch_rel:
            integrity_issues.append("Manifest item missing original_path/patched_path.")
            continue

        patched_file = _resolve_inside(patch_dir, patch_rel, "patched file")
        original_file = _resolve_inside(game_root, original_rel, "game file")

        if not patched_file.exists():
            integrity_issues.append(f"Missing patched file: {patch_rel}")
            continue

        expected_patched_sha = item.get("patched_sha256")
        if expected_patched_sha:
            actual_patched_sha = sha256_file(patched_file)
            if actual_patched_sha != expected_patched_sha:
                integrity_issues.append(
                    f"Patched file hash mismatch: {patch_rel}"
                )

        expected_original_sha = item.get("original_sha256")
        if expected_original_sha:
            if not original_file.exists():
                original_issues.append(
                    f"Original file missing but expected: {original_rel}"
                )
            else:
                actual_original_sha = sha256_file(original_file)
                if actual_original_sha != expected_original_sha:
                    original_issues.append(
                        f"Original file hash mismatch: {original_rel}. "
                        "Game files may have changed since patch creation."
                    )
        elif "original_sha256" in item and original_file.exists():
            original_issues.append(
                f"Original file now exists but patch expected a new file: {original_rel}."
            )

    # --force may intentionally accept a changed game version, but it must
    # never install an incomplete or tampered patch payload.
    blocking_issues = integrity_issues + ([] if force else original_issues)
    if blocking_issues:
        raise RuntimeError(
            "Patch verification failed:\n  - " + "\n  - ".join(blocking_issues)
        )

    return integrity_issues + original_issues


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

        original_file = _resolve_inside(game_root, rel_path, "game file")

        record = {
            "game_path": rel_path,
            "backup_path": rel_path,
            "existed_before": original_file.exists(),
            "installed_sha256": item.get("patched_sha256"),
        }

        if original_file.exists():
            backup_file = _resolve_inside(backup_dir, rel_path, "backup file")
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

    atomic_write_text(
        backup_dir / "manifest.json",
        json.dumps(backup_manifest, ensure_ascii=False, indent=2),
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

    try:
        for item in manifest.get("files", []):
            original_rel = item.get("original_path") or item.get("patched_path")
            patch_rel = item.get("patched_path") or item.get("original_path")

            if not original_rel or not patch_rel:
                continue

            source_file = _resolve_inside(patch_dir, patch_rel, "patched file")
            target_file = _resolve_inside(game_root, original_rel, "game file")

            if not source_file.exists():
                continue

            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)

            installed.append(original_rel)
    except Exception as install_error:
        if backup_id is None:
            raise
        try:
            rollback(
                project_dir=project_dir,
                backup_id=backup_id,
                game_root=game_root,
                force=True,
            )
        except Exception as rollback_error:
            raise RuntimeError(
                "Patch installation failed and automatic rollback also failed. "
                f"Install error: {install_error}; rollback error: {rollback_error}"
            ) from install_error
        raise RuntimeError(
            f"Patch installation failed; original files were restored: {install_error}"
        ) from install_error

    project_dir.mkdir(parents=True, exist_ok=True)

    last_install = {
        "installed_at": _now_iso(),
        "patch_dir": str(patch_dir),
        "game_root": str(game_root),
        "backup_id": backup_id,
        "files": installed,
    }

    atomic_write_text(
        project_dir / "last_install.json",
        json.dumps(last_install, ensure_ascii=False, indent=2),
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

    backups_root = project_dir / "backups"
    backup_dir = _resolve_inside(backups_root, backup_id, "backup")
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

        target_file = _resolve_inside(game_root, rel_path, "game file")
        installed_sha = item.get("installed_sha256")
        if not force and installed_sha:
            if not target_file.exists():
                raise RuntimeError(
                    f"Cannot safely rollback; installed file is missing: {rel_path}"
                )
            if sha256_file(target_file) != installed_sha:
                raise RuntimeError(
                    f"Cannot safely rollback; installed file changed: {rel_path}. "
                    "Use force only after reviewing the file."
                )

        if item.get("existed_before"):
            backup_file = _resolve_inside(
                backup_dir,
                item.get("backup_path", rel_path),
                "backup file",
            )

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
