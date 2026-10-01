from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..project import ProjectStore


LogFunc = Callable[[str], None]


def _make_log(log: Optional[LogFunc]) -> LogFunc:
    if log is None:
        return lambda message: None
    return log


def get_project_info(project_dir: Path | str) -> Dict[str, Any]:
    with ProjectStore(project_dir) as store:
        return {
            "meta": store.meta,
            "stats": store.stats_by_status(),
        }


def export_project_csv(
    project_dir: Path | str,
    status: Optional[str] = None,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    if status is not None and not str(status).strip():
        status = None

    with ProjectStore(project_dir) as store:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        exports_dir = Path(store.project_dir) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        output_path = exports_dir / f"export_{timestamp}.csv"

        count = store.export_csv(
            output_path=output_path,
            status=status,
        )

        log(f"Exported {count} entries to {output_path}")

        return {
            "path": str(output_path.resolve()),
            "count": count,
        }


def import_project_csv(
    project_dir: Path | str,
    csv_path: Path | str,
    overwrite_locked: bool = False,
    log: Optional[LogFunc] = None,
) -> Dict[str, Any]:
    log = _make_log(log)

    with ProjectStore(project_dir) as store:
        stats = store.import_csv(
            input_path=csv_path,
            overwrite_locked=overwrite_locked,
        )

        store.save_meta()

        log(
            f"CSV import complete. "
            f"updated={stats.get('updated', 0)} "
            f"unchanged={stats.get('unchanged', 0)} "
            f"missing={stats.get('missing', 0)} "
            f"locked={stats.get('locked', 0)} "
            f"invalid={stats.get('invalid', 0)}"
        )

        return stats
