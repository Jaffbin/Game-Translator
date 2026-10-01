"""One-task scan, translation and QA for the project console."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import load_config
from .quality import run_qa_project
from .scan import scan_project
from .translation import translate_project


class WorkflowError(RuntimeError):
    """Expected, user-actionable interruption of an automatic run."""


def run_automatic_workflow(
    project_dir: Path | str,
    log: Callable[[str], None],
    provider_id: str | None = None,
) -> dict[str, Any]:
    config = load_config()
    provider_id = provider_id or config.first_available_provider()
    if provider_id == "mock":
        raise WorkflowError("没有可用的 AI Provider。请先在 Settings 配置并测试 API Key；自动流程不会生成模拟翻译。")
    if config.get_provider(provider_id) is None:
        raise WorkflowError(f"找不到 Provider：{provider_id}。请在 Settings 检查配置。")
    provider = config.get_provider(provider_id)
    if not provider.is_ready or (provider.is_local_endpoint and not provider.local_endpoint_reachable()):
        raise WorkflowError(f"Provider {provider_id} 尚未就绪。请在 Settings 检查 API Key 或本地服务。")

    log("Step 1/3: Scan game text")
    scan = scan_project(project_dir=project_dir, log=log)
    if scan.get("cancelled") or scan.get("errors"):
        raise WorkflowError(f"扫描未完成：{scan.get('errors', 0)} 个文件读取失败。请查看上方日志。")
    if not scan.get("entries"):
        raise WorkflowError("扫描完成，但未找到可翻译文本。请检查所选游戏目录和引擎支持范围。")

    log(f"Step 2/3: Translate with {provider_id}")
    translation = translate_project(
        project_dir=project_dir,
        provider_id=provider_id,
        log=log,
        stop_on_provider_error=True,
    )
    if translation.get("failed") or translation.get("cancelled"):
        reason = translation.get("fatal_error")
        raise WorkflowError(
            f"翻译未全部完成：{translation.get('failed', 0)} 条失败。"
            + (f"Provider 原因：{reason}。" if reason else "QA 已跳过，已完成的译文会保留；请查看日志中的 [ERROR]。")
        )
    log("Step 3/3: QA")
    qa = run_qa_project(project_dir=project_dir, apply=True, apply_warnings=True, log=log)
    return {"scan": scan, "translation": translation, "qa": qa}
