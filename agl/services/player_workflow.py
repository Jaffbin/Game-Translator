from __future__ import annotations

import datetime
import math
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .. import workspace_projects
from ..config import load_config
from ..engines import detect_handler, resolve_game_folder
from ..project import ProjectStore
from ..workspace import projects_root, sanitize_project_name
from . import delivery, quality, scan, translation
from .diagnostics import diagnose_game


class PlayerWorkflow:
    """Stateful, resumable-in-session workflow for the simple player UI.

    Preparation is deliberately separated from translation.  This gives the user
    a chance to verify the detected engine and workload before any cloud request
    is made.  Applying the generated patch is a separate, confirmed operation.
    """

    BUSY_STATES = {"preparing", "translating", "checking_qa", "applying", "restoring"}

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._data: Dict[str, Any] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._cancel.clear()
            self._data = {
                "state": "idle",
                "step": "",
                "message": "",
                "logs": [],
                "project": None,
                "project_dir": None,
                "patch": None,
                "backup_count": 0,
                "engine": None,
                "provider": None,
                "entries": 0,
                "characters": 0,
                "estimated_tokens": 0,
                "processed_entries": 0,
                "progress_percent": 0,
                "qa": None,
                "result": None,
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._data)
            result["logs"] = list(self._data["logs"][-500:])
            result["busy"] = self._data["state"] in self.BUSY_STATES
            result["can_cancel"] = self._data["state"] in {"preparing", "translating"}
            return result

    def preflight(self, game_path: Path | str, target_language: str = "zh-CN") -> Dict[str, Any]:
        selected = Path(game_path).expanduser().resolve()
        path, handler = resolve_game_folder(selected)
        config = load_config()
        provider_id = config.first_available_provider()
        provider = config.get_provider(provider_id)
        diagnostics = diagnose_game(path, target_language)
        return {
            "path": str(path),
            "selected_path": str(selected),
            "auto_resolved": path != selected,
            "engine": handler.engine_id,
            "provider": provider_id,
            "provider_ready": provider_id != "mock",
            "provider_mode": (
                "local" if provider and provider.is_local_endpoint
                else "cloud" if provider_id != "mock"
                else None
            ),
            "diagnostics": diagnostics,
        }

    def prepare(self, game_path: Path | str, target_language: str) -> None:
        path = Path(game_path).expanduser().resolve()
        self._start("preparing", lambda: self._prepare_job(path, target_language))

    def start_auto(self, game_path: Path | str, target_language: str) -> None:
        """Run scan, translation and QA from one explicit player action."""
        path = Path(game_path).expanduser().resolve()
        self._start("preparing", lambda: self._prepare_job(path, target_language, auto_translate=True))

    def recovery(self, game_path: Path | str, target_language: str) -> Dict[str, Any]:
        """Find an existing matching project and expose rollback without a provider."""
        with self._lock:
            if self._data["state"] in self.BUSY_STATES:
                raise RuntimeError("请等待当前任务完成后再检查备份。")
        resolved = Path(game_path).expanduser().resolve()
        for project in workspace_projects.list_projects():
            try:
                if (
                    Path(project["game_path"]).resolve() == resolved
                    and project.get("target_language") == target_language
                ):
                    backups = delivery.list_backups_project(project["path"])
                    self._set(
                        project=project["name"],
                        project_dir=project["path"],
                        backup_count=len(backups),
                    )
                    return {
                        "project": project["name"],
                        "backup_count": len(backups),
                    }
            except (KeyError, OSError, ValueError):
                continue
        self._set(backup_count=0)
        return {"project": None, "backup_count": 0}

    def translate(self) -> None:
        with self._lock:
            if self._data["state"] not in {"prepared", "needs_attention"}:
                raise RuntimeError("请先完成游戏扫描和翻译预览。")
            project_dir = self._data["project_dir"]
            provider_id = self._data["provider"]
        if not project_dir or not provider_id or provider_id == "mock":
            raise RuntimeError("请先在设置中配置可用的云端翻译 Provider。")
        self._start("translating", lambda: self._translate_job(Path(project_dir), provider_id))

    def rerun_qa(self) -> None:
        with self._lock:
            if self._data["state"] not in {"done", "needs_attention"}:
                raise RuntimeError("请先完成翻译，才能重新运行 QA。")
            project_dir = self._data.get("project_dir")
        if not project_dir:
            raise RuntimeError("找不到翻译项目，无法运行 QA。")
        self._start("checking_qa", lambda: self._rerun_qa_job(Path(project_dir)))

    def apply(self) -> None:
        with self._lock:
            if self._data["state"] != "done":
                raise RuntimeError("当前没有可应用的翻译补丁。")
            project_dir = self._data["project_dir"]
            patch = self._data["patch"]
        if not project_dir or not patch:
            raise RuntimeError("翻译补丁信息不完整。")
        self._start("applying", lambda: self._apply_job(Path(project_dir), Path(patch)))

    def restore(self) -> None:
        with self._lock:
            if self._data["state"] in self.BUSY_STATES:
                raise RuntimeError("请等待当前任务完成后再恢复。")
            if not self._data.get("backup_count") and self._data["state"] not in {"applied", "error"}:
                raise RuntimeError("当前没有需要恢复的已应用补丁。")
            project_dir = self._data["project_dir"]
        if not project_dir:
            raise RuntimeError("项目路径不可用。")
        self._start("restoring", lambda: self._restore_job(Path(project_dir)))

    def cancel(self) -> None:
        with self._lock:
            if self._data["state"] not in {"preparing", "translating"}:
                raise RuntimeError("当前没有可取消的任务。")
            self._cancel.set()
            self._data["message"] = "正在安全停止…"

    def _start(self, state: str, target: Callable[[], None]) -> None:
        with self._lock:
            if self._data["state"] in self.BUSY_STATES:
                raise RuntimeError("已有任务正在运行，请等待或先取消。")
            self._cancel.clear()
            self._data.update(state=state, message="", result=None)

        def runner() -> None:
            try:
                target()
            except Exception as exc:
                self._set(state="error", step="出错", message=str(exc))
                self._log(f"[ERROR] {exc}")

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

    def _set(self, **values: Any) -> None:
        with self._lock:
            self._data.update(values)

    def _log(self, message: str) -> None:
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._data["logs"].append(f"[{stamp}] {message}")
            if len(self._data["logs"]) > 2000:
                self._data["logs"] = self._data["logs"][-2000:]

    def _check_cancelled(self) -> bool:
        if not self._cancel.is_set():
            return False
        self._set(state="cancelled", step="已取消", message="任务已安全停止，可以重新开始。")
        self._log("Task cancelled by user.")
        return True

    def _find_or_create_project(self, game_path: Path, target_language: str) -> tuple[str, Path]:
        for project in workspace_projects.list_projects():
            try:
                if (
                    Path(project["game_path"]).resolve() == game_path
                    and project.get("target_language") == target_language
                ):
                    return project["name"], Path(project["path"])
            except (KeyError, OSError, ValueError):
                continue

        base_name = sanitize_project_name(f"{game_path.name}_{target_language}")
        candidate = base_name
        index = 1
        while (projects_root() / candidate).exists():
            index += 1
            candidate = f"{base_name}_{index}"
        result = workspace_projects.create_project(candidate, str(game_path), target_language)
        return result["name"], Path(result["path"])

    def _prepare_job(self, game_path: Path, target_language: str, auto_translate: bool = False) -> None:
        self._set(step="检查游戏", message="正在识别游戏引擎…", logs=[])
        preflight = self.preflight(game_path, target_language)
        self._set(engine=preflight["engine"], provider=preflight["provider"])
        self._log(f"Detected engine: {preflight['engine']}")
        if not preflight.get("diagnostics", {}).get("can_scan", True):
            self._set(
                state="error",
                step="环境检查",
                message="游戏文件存在编码或空间问题，请查看选择游戏后的诊断结果。",
            )
            return
        if not preflight["provider_ready"]:
            self._set(
                state="configuration_required",
                step="需要设置",
                message=(
                    "未找到可用的翻译 Provider。可以启动本地 Ollama，或稍后配置云端 API Key；"
                    "普通模式不会使用模拟翻译。"
                ),
            )
            return
        if self._check_cancelled():
            return

        self._set(step="准备项目", message="正在创建或打开本地翻译项目…")
        project_name, project_dir = self._find_or_create_project(game_path, target_language)
        backups = delivery.list_backups_project(project_dir)
        self._set(
            project=project_name,
            project_dir=str(project_dir.resolve()),
            backup_count=len(backups),
        )
        if self._check_cancelled():
            return

        self._set(step="扫描文本", message="正在读取游戏中的可翻译文本…")
        scan_result = scan.scan_project(
            project_dir,
            log=self._log,
            should_cancel=self._cancel.is_set,
        )
        if scan_result.get("cancelled") or self._check_cancelled():
            return
        scan_errors = int(scan_result.get("errors", 0))
        if scan_errors:
            self._set(
                state="error",
                step="扫描未完成",
                message=(
                    f"有 {scan_errors} 个游戏文件无法安全读取。普通模式已停止，"
                    "以避免生成不完整补丁；请检查日志后重试。"
                ),
            )
            return
        if int(scan_result.get("entries", 0)) <= 0:
            self._set(state="warning", step="扫描完成", message="没有找到可翻译文本。")
            return

        with ProjectStore(project_dir) as store:
            entries = store.all_entries()
        characters = sum(len(entry.source_text or "") for entry in entries)
        estimated_tokens = int(math.ceil(characters / 3.2))
        self._set(
            state="prepared",
            step="等待确认",
            message="扫描完成。确认预览后才会向云端发送文本。",
            entries=len(entries),
            characters=characters,
            estimated_tokens=estimated_tokens,
        )
        self._log(
            f"Prepared {len(entries)} entries, {characters} source characters, "
            f"approximately {estimated_tokens} input tokens."
        )
        if auto_translate and not self._check_cancelled():
            self._set(state="translating", step="自动翻译", message="扫描完成，正在开始翻译…")
            self._translate_job(project_dir, preflight["provider"])

    def _translate_job(self, project_dir: Path, provider_id: str) -> None:
        self._set(step="云端翻译", message="正在翻译文本，请勿关闭应用…")

        def progress(data: Dict[str, int]) -> None:
            total = max(1, int(data.get("total", 0)))
            processed = int(data.get("processed", 0))
            self._set(
                processed_entries=processed,
                entries=int(data.get("total", 0)),
                progress_percent=min(100, int(processed * 100 / total)),
                message=f"正在翻译 {processed}/{data.get('total', 0)} 条文本…",
            )

        result = translation.translate_project(
            project_dir,
            provider_id=provider_id,
            log=self._log,
            should_cancel=self._cancel.is_set,
            progress=progress,
            stop_on_provider_error=True,
        )
        if result.get("cancelled") or self._check_cancelled():
            return

        self._set(step="质量检查", message="正在检查占位符、术语和译文完整性…")
        qa_result = quality.run_qa_project(
            project_dir,
            apply=True,
            apply_warnings=True,
            log=self._log,
        )
        if self._check_cancelled():
            return

        failed = int(result.get("failed", 0))
        qa_errors = int(qa_result.get("entries_with_errors", 0))
        qa_warnings = int(qa_result.get("entries_with_warnings", 0))
        if failed or qa_errors or qa_warnings:
            self._set(
                state="needs_attention",
                step="需要处理",
                message=(
                    f"有 {failed} 条翻译失败、{qa_errors} 条质量错误、"
                    f"{qa_warnings} 条质量警告。"
                    "为避免生成不完整补丁，普通模式已暂停。可以重试或进入专业工作区检查。"
                ),
                qa=qa_result,
                result={"translation": result, "qa": qa_result},
            )
            self._log("Patch generation stopped because translation or QA issues remain.")
            return

        self._set(step="生成补丁", message="正在准备可安装的翻译补丁…", qa=qa_result)
        patch_result = delivery.patch_project(project_dir, patch_name=None, log=self._log)
        self._set(
            state="done",
            step="完成",
            message="翻译和质量检查已完成。查看结果后可以应用到游戏。",
            patch=patch_result.get("patch_dir"),
            result={"translation": result, "qa": qa_result, "patch": patch_result},
        )

    def _rerun_qa_job(self, project_dir: Path) -> None:
        self._set(step="重新 QA", message="正在重新检查译文…")
        qa_result = quality.run_qa_project(
            project_dir, apply=True, apply_warnings=True, log=self._log,
        )
        errors = int(qa_result.get("entries_with_errors", 0))
        warnings = int(qa_result.get("entries_with_warnings", 0))
        if errors or warnings:
            self._set(
                state="needs_attention", step="QA 仍需处理",
                message=f"QA 发现 {errors} 个错误、{warnings} 个警告。请查看日志和专业工作区。",
                patch=None, qa=qa_result,
            )
            return
        self._set(step="生成补丁", message="QA 通过，正在重新准备补丁…")
        patch_result = delivery.patch_project(project_dir, patch_name=None, log=self._log)
        self._set(
            state="done", step="QA 已通过",
            message="QA 已重新检查通过，补丁已更新，可以应用到游戏。",
            patch=patch_result.get("patch_dir"), qa=qa_result,
        )

    def _apply_job(self, project_dir: Path, patch: Path) -> None:
        self._set(step="自动备份", message="正在备份并应用翻译补丁…")
        result = delivery.install_project(
            project_dir, patch_path=patch, force=False, backup=True, log=self._log
        )
        backups = delivery.list_backups_project(project_dir)
        self._set(
            state="applied",
            step="已应用",
            message="翻译已经应用到游戏。",
            result=result,
            backup_count=len(backups),
        )

    def _restore_job(self, project_dir: Path) -> None:
        self._set(step="恢复原始文件", message="正在从最近一次备份恢复…")
        result = delivery.rollback_project(project_dir, backup_id=None, force=False, log=self._log)
        backups = delivery.list_backups_project(project_dir)
        self._set(
            state="restored",
            step="已恢复",
            message="游戏文件已恢复。",
            result=result,
            backup_count=len(backups),
        )
