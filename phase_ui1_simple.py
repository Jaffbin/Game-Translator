from __future__ import annotations

import argparse
import datetime
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agl import operations, workspace_projects
from agl.config import load_config
from agl.workspace import projects_root, sanitize_project_name


# ----------------------------------------------------------------------
# Simple task state
# ----------------------------------------------------------------------

class SimpleTaskState:
    """
    Very simple global UI task state.

    This is for local single-user usage only.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state: str = "idle"
        self.step: str = ""
        self.message: str = ""
        self.logs: List[str] = []
        self.project: Optional[str] = None
        self.project_dir: Optional[str] = None
        self.patch: Optional[str] = None

    def log(self, message: str) -> None:
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")

        # Keep logs from growing forever.
        if len(self.logs) > 2000:
            self.logs = self.logs[-2000:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "step": self.step,
            "message": self.message,
            "logs": self.logs[-500:],
            "project": self.project,
            "project_dir": self.project_dir,
            "patch": self.patch,
        }


TASK = SimpleTaskState()


def _is_busy() -> bool:
    return TASK.state in {
        "running",
        "applying",
        "restoring",
    }


def _run_thread(target) -> None:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _find_or_create_project(
    game_path: Path,
    target_language: str,
) -> tuple[str, Path]:
    """
    Find an existing project for this game path and language.
    If not found, create a new one automatically.
    """
    projects = workspace_projects.list_projects()

    resolved_game_path = game_path.resolve()

    for project in projects:
        try:
            project_game_path = Path(project["game_path"]).resolve()
        except Exception:
            continue

        if (
            project_game_path == resolved_game_path
            and project.get("target_language") == target_language
        ):
            return project["name"], Path(project["path"])

    base_name = sanitize_project_name(
        f"{game_path.name}_{target_language}"
    )

    candidate = base_name
    index = 1

    while (projects_root() / candidate).exists():
        index += 1
        candidate = f"{base_name}_{index}"

    result = workspace_projects.create_project(
        name=candidate,
        game_path=str(resolved_game_path),
        target_language=target_language,
    )

    return result["name"], Path(result["path"])


# ----------------------------------------------------------------------
# Background jobs
# ----------------------------------------------------------------------

def _job_start(game_path: Path, target_language: str) -> None:
    try:
        TASK.state = "running"
        TASK.step = "检查游戏"
        TASK.message = "正在检查游戏路径..."
        TASK.log(f"Game path: {game_path}")
        TASK.log(f"Target language: {target_language}")

        if not game_path.exists():
            raise FileNotFoundError(
                f"Game path does not exist: {game_path}"
            )

        TASK.step = "准备项目"
        TASK.message = "正在准备翻译项目..."
        TASK.log("Looking for existing project or creating a new one...")

        project_name, project_dir = _find_or_create_project(
            game_path=game_path,
            target_language=target_language,
        )

        TASK.project = project_name
        TASK.project_dir = str(project_dir.resolve())

        TASK.log(f"Project: {project_name}")
        TASK.log(f"Project folder: {project_dir}")

        # --------------------------------------------------------------
        # Scan
        # --------------------------------------------------------------

        TASK.step = "读取游戏文本"
        TASK.message = "正在读取游戏文本..."
        TASK.log("Scanning game files...")

        scan_result = operations.scan_project(
            project_dir=project_dir,
            log=TASK.log,
        )

        extracted = int(scan_result.get("entries", 0))

        if extracted <= 0:
            TASK.state = "warning"
            TASK.step = "完成"
            TASK.message = (
                "未找到可翻译文本。"
                "该游戏可能不受当前版本支持，"
                "或文本被打包在二进制资源里。"
            )
            TASK.log("No translatable entries found.")
            return

        # --------------------------------------------------------------
        # Translate
        # --------------------------------------------------------------

        TASK.step = "翻译文本"
        TASK.message = "正在翻译游戏文本..."
        TASK.log("Translating entries...")

        operations.translate_project(
            project_dir=project_dir,
            provider_id=None,
            model=None,
            retranslate=False,
            no_cache=False,
            log=TASK.log,
        )

        # --------------------------------------------------------------
        # Patch
        # --------------------------------------------------------------

        TASK.step = "生成汉化补丁"
        TASK.message = "正在生成补丁..."
        TASK.log("Generating patch...")

        patch_result = operations.patch_project(
            project_dir=project_dir,
            patch_name=None,
            log=TASK.log,
        )

        TASK.patch = patch_result.get("patch_dir")

        TASK.state = "done"
        TASK.step = "完成"
        TASK.message = (
            "翻译完成，补丁已生成。"
            "你可以点击“应用到游戏”。"
        )

        TASK.log(f"Patch created: {TASK.patch}")

    except Exception as exc:
        TASK.state = "error"
        TASK.step = "出错"
        TASK.message = str(exc)
        TASK.log(f"[ERROR] {exc}")


def _job_apply() -> None:
    try:
        if not TASK.project_dir:
            raise RuntimeError("No project is ready.")

        if not TASK.patch:
            raise RuntimeError("No patch is available.")

        TASK.state = "applying"
        TASK.step = "应用补丁"
        TASK.message = "正在应用补丁到游戏..."
        TASK.log("Installing patch...")

        result = operations.install_project(
            project_dir=TASK.project_dir,
            patch_path=TASK.patch,
            force=False,
            backup=True,
            log=TASK.log,
        )

        TASK.state = "applied"
        TASK.step = "已应用"
        TASK.message = (
            "补丁已应用到游戏。"
            "如果出现问题，可以点击“恢复原始文件”。"
        )

        TASK.log(f"Backup ID: {result.get('backup_id')}")
        TASK.log(f"Installed files: {result.get('installed', 0)}")

    except Exception as exc:
        TASK.state = "error"
        TASK.step = "出错"
        TASK.message = str(exc)
        TASK.log(f"[ERROR] {exc}")


def _job_restore() -> None:
    try:
        if not TASK.project_dir:
            raise RuntimeError("No project is ready.")

        TASK.state = "restoring"
        TASK.step = "恢复原始文件"
        TASK.message = "正在恢复原始文件..."
        TASK.log("Rolling back...")

        result = operations.rollback_project(
            project_dir=TASK.project_dir,
            backup_id=None,
            force=False,
            log=TASK.log,
        )

        TASK.state = "restored"
        TASK.step = "已恢复"
        TASK.message = "已恢复原始文件。"

        TASK.log(f"Backup ID: {result.get('backup_id')}")
        TASK.log(f"Restored files: {result.get('restored', 0)}")
        TASK.log(f"Deleted files: {result.get('deleted', 0)}")

    except Exception as exc:
        TASK.state = "error"
        TASK.step = "出错"
        TASK.message = str(exc)
        TASK.log(f"[ERROR] {exc}")


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------

class StartRequest(BaseModel):
    game_path: str
    target_language: str = "zh-CN"


# ----------------------------------------------------------------------
# HTML page
# ----------------------------------------------------------------------

PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer 简单模式</title>
  <style>
    body {
      font-family: "Segoe UI", Arial, sans-serif;
      margin: 0;
      background: #f2f4f8;
    }

    .container {
      max-width: 980px;
      margin: 24px auto;
      padding: 0 16px;
    }

    h1 {
      font-size: 28px;
      margin-bottom: 16px;
    }

    .card {
      background: #fff;
      border: 1px solid #d8dee8;
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.05);
    }

    .card h2 {
      margin: 0 0 12px 0;
      font-size: 18px;
    }

    input[type=text] {
      width: 620px;
      max-width: 100%;
      padding: 10px;
      font-size: 15px;
      border: 1px solid #bbb;
      border-radius: 6px;
    }

    select {
      padding: 10px;
      font-size: 15px;
      border: 1px solid #bbb;
      border-radius: 6px;
      margin-left: 8px;
    }

    button {
      padding: 10px 16px;
      font-size: 15px;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      margin-left: 8px;
    }

    button.primary {
      background: #2563eb;
      color: #fff;
    }

    button.danger {
      background: #b91c1c;
      color: #fff;
    }

    button.secondary {
      background: #475569;
      color: #fff;
    }

    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    #state_text {
      font-size: 20px;
      font-weight: bold;
      margin-bottom: 10px;
    }

    #log {
      height: 280px;
      overflow: auto;
      background: #0f172a;
      color: #d1fae5;
      font-family: Consolas, monospace;
      font-size: 12px;
      padding: 10px;
      border-radius: 8px;
      white-space: pre-wrap;
    }

    .small {
      color: #555;
      font-size: 13px;
      margin-top: 8px;
    }

    .warning {
      color: #b45309;
      font-weight: bold;
    }

    .ok {
      color: #166534;
      font-weight: bold;
    }

    .error {
      color: #b91c1c;
      font-weight: bold;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>AutoGame Localizer</h1>

    <div class="card">
      <h2>1. 选择游戏</h2>

      <div>
        <input id="game_path" type="text" placeholder="请粘贴游戏文件夹路径，例如 D:\\Games\\MyGame">

        <select id="target_language">
          <option value="zh-CN">简体中文</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </select>

        <button id="start_btn" class="primary" onclick="start()">开始翻译</button>
      </div>

      <div id="service" class="small">正在检查翻译服务...</div>
      <div class="small">
        注意：请选择游戏文件夹，不要选择 .exe 文件。
      </div>
    </div>

    <div class="card">
      <h2>2. 翻译状态</h2>
      <div id="state_text">等待开始</div>
      <pre id="log"></pre>
    </div>

    <div class="card">
      <h2>3. 应用到游戏</h2>
      <div id="patch_info" class="small"></div>

      <div style="margin-top:10px;">
        <button id="apply_btn" class="secondary" onclick="applyPatch()" disabled>应用到游戏</button>
        <button id="restore_btn" class="danger" onclick="restore()" disabled>恢复原始文件</button>
      </div>

      <div class="small">
        应用到游戏前会自动备份原文件。恢复原始文件会使用最近一次备份。
      </div>
    </div>
  </div>

  <script>
    let polling = false;

    function busyState(state) {
      return state === "running" || state === "applying" || state === "restoring";
    }

    function friendlyState(state) {
      if (state === "idle") return "等待开始";
      if (state === "running") return "正在处理...";
      if (state === "done") return "翻译完成";
      if (state === "warning") return "完成，但有提醒";
      if (state === "error") return "出现问题";
      if (state === "applying") return "正在应用补丁...";
      if (state === "applied") return "已应用到游戏";
      if (state === "restoring") return "正在恢复原始文件...";
      if (state === "restored") return "已恢复原始文件";
      return state;
    }

    async function api(url, method = "GET", body = null) {
      const options = {
        method: method,
        headers: {}
      };

      if (body !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }

      const response = await fetch(url, options);
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.detail || response.statusText);
      }

      return data;
    }

    async function refreshService() {
      try {
        const data = await api("/api/service");

        const el = document.getElementById("service");

        if (data.is_mock) {
          el.innerHTML =
            "<span class='warning'>当前没有配置真实翻译服务，处于测试模式。生成的翻译不是真正中文。</span>";
        } else {
          el.innerHTML =
            "<span class='ok'>翻译服务已配置：" + data.provider_id + "</span>";
        }
      } catch (err) {
        document.getElementById("service").textContent =
          "无法检查翻译服务: " + err.message;
      }
    }

    async function refreshState() {
      try {
        const state = await api("/api/state");
        renderState(state);

        if (busyState(state.state)) {
          setTimeout(refreshState, 1500);
        } else {
          polling = false;
        }
      } catch (err) {
        console.error(err);
        polling = false;
      }
    }

    function startPolling() {
      if (!polling) {
        polling = true;
        refreshState();
      }
    }

    function renderState(state) {
      const stateText = document.getElementById("state_text");
      const log = document.getElementById("log");
      const patchInfo = document.getElementById("patch_info");

      const startBtn = document.getElementById("start_btn");
      const applyBtn = document.getElementById("apply_btn");
      const restoreBtn = document.getElementById("restore_btn");

      const busy = busyState(state.state);

      let text = friendlyState(state.state);

      if (state.step) {
        text += " - " + state.step;
      }

      if (state.message) {
        text += "\\n" + state.message;
      }

      stateText.textContent = text;

      if (state.state === "error") {
        stateText.className = "error";
      } else if (state.state === "warning") {
        stateText.className = "warning";
      } else if (state.state === "done" || state.state === "applied" || state.state === "restored") {
        stateText.className = "ok";
      } else {
        stateText.className = "";
      }

      log.textContent = (state.logs || []).join("\\n");
      log.scrollTop = log.scrollHeight;

      if (state.patch) {
        patchInfo.textContent = "补丁位置：" + state.patch;
      } else {
        patchInfo.textContent = "还没有生成补丁。";
      }

      startBtn.disabled = busy;
      applyBtn.disabled = busy || !state.patch;
      restoreBtn.disabled = busy || !state.project_dir;
    }

    async function start() {
      const gamePath = document.getElementById("game_path").value.trim();
      const targetLanguage = document.getElementById("target_language").value;

      if (!gamePath) {
        alert("请先粘贴游戏文件夹路径。");
        return;
      }

      try {
        await api("/api/start", "POST", {
          game_path: gamePath,
          target_language: targetLanguage
        });

        startPolling();
      } catch (err) {
        alert(err.message);
      }
    }

    async function applyPatch() {
      if (!confirm("将把翻译补丁应用到游戏。\\n系统会先自动备份原文件。\\n确定继续吗？")) {
        return;
      }

      try {
        await api("/api/apply", "POST");
        startPolling();
      } catch (err) {
        alert(err.message);
      }
    }

    async function restore() {
      if (!confirm("确定要恢复原始文件吗？")) {
        return;
      }

      try {
        await api("/api/restore", "POST");
        startPolling();
      } catch (err) {
        alert(err.message);
      }
    }

    refreshService();
    refreshState();
  </script>
</body>
</html>
"""


# ----------------------------------------------------------------------
# App
# ----------------------------------------------------------------------

def create_simple_app() -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Simple Mode")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def api_state():
        return TASK.to_dict()

    @app.get("/api/service")
    def api_service():
        try:
            config = load_config()
            provider_id = config.first_available_provider()
            provider_config = config.get_provider(provider_id)

            if provider_config is None:
                return {
                    "provider_id": "mock",
                    "model": "",
                    "is_mock": True,
                    "has_key": False,
                }

            is_mock = provider_config.type == "mock" or provider_id == "mock"

            return {
                "provider_id": provider_id,
                "model": provider_config.model,
                "is_mock": is_mock,
                "has_key": bool(provider_config.api_key),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/start")
    def api_start(body: StartRequest):
        if _is_busy():
            raise HTTPException(
                status_code=409,
                detail="A task is already running.",
            )

        game_path = Path(body.game_path).expanduser()

        if not str(game_path).strip():
            raise HTTPException(
                status_code=400,
                detail="Game path is required.",
            )

        if not game_path.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Game path does not exist: {game_path}",
            )

        target_language = body.target_language.strip() or "zh-CN"

        TASK.reset()
        TASK.log("Simple translation started.")

        _run_thread(
            lambda: _job_start(
                game_path.resolve(),
                target_language,
            )
        )

        return {"ok": True}

    @app.post("/api/apply")
    def api_apply():
        if _is_busy():
            raise HTTPException(
                status_code=409,
                detail="A task is already running.",
            )

        if not TASK.project_dir:
            raise HTTPException(
                status_code=400,
                detail="No project is ready. Please start translation first.",
            )

        if not TASK.patch:
            raise HTTPException(
                status_code=400,
                detail="No patch is available. Please start translation first.",
            )

        _run_thread(_job_apply)

        return {"ok": True}

    @app.post("/api/restore")
    def api_restore():
        if _is_busy():
            raise HTTPException(
                status_code=409,
                detail="A task is already running.",
            )

        if not TASK.project_dir:
            raise HTTPException(
                status_code=400,
                detail="No project is ready. Please start translation first.",
            )

        _run_thread(_job_restore)

        return {"ok": True}

    return app


# ----------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="UI Phase 1: simple mode for normal users"
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8200,
    )

    args = parser.parse_args()

    app = create_simple_app()

    print("Opening AutoGame Localizer Simple Mode")
    print(f"Visit: http://{args.host}:{args.port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()