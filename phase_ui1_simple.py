from __future__ import annotations

import os
import subprocess
import sys
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

def select_folder_native() -> str:
    """Open a native folder picker via PowerShell (Windows, thread-safe)."""
    if not sys.platform.startswith("win"):
        return ""

    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.ShowNewFolderButton = $false; "
        "$d.Description = 'Select game folder'; "
        "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath } else { '' }"
    )

    try:
        result = subprocess.run(
            ["powershell", "-STA", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return (result.stdout or "").strip()
    except Exception as exc:
        print(f"Folder dialog error: {exc}")
        return ""

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
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>普通玩家模式</title>
  <style>
    :root {
      --bg: #f8fafc; --surface: #ffffff; --primary: #2563eb; --primary-hover: #1d4ed8;
      --text-main: #0f172a; --text-muted: #64748b; --border: #e2e8f0;
      --success: #10b981; --warning: #f59e0b; --danger: #ef4444;
      --radius: 12px; --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05);
    }
    body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text-main); margin: 0; padding: 0; }
    
    /* Top Bar */
    .topbar { background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; }
    .topbar h2 { margin: 0; font-size: 18px; display: flex; align-items: center; gap: 8px; }
    .back-btn { text-decoration: none; color: var(--text-muted); font-size: 14px; display: flex; align-items: center; gap: 4px; }
    .back-btn:hover { color: var(--primary); }

    /* Layout */
    .container { max-width: 800px; margin: 40px auto; padding: 0 20px; }
    
    /* Steps Indicator */
    .steps { display: flex; justify-content: space-between; margin-bottom: 40px; position: relative; }
    .steps::before { content: ''; position: absolute; top: 15px; left: 0; right: 0; height: 2px; background: var(--border); z-index: 0; }
    .step { background: var(--bg); padding: 0 12px; z-index: 1; text-align: center; flex: 1; }
    .step-num { width: 32px; height: 32px; border-radius: 50%; background: var(--surface); border: 2px solid var(--border); display: flex; align-items: center; justify-content: center; margin: 0 auto 8px; font-weight: bold; color: var(--text-muted); }
    .step.active .step-num { border-color: var(--primary); color: var(--primary); background: #eff6ff; }
    .step.done .step-num { border-color: var(--success); background: var(--success); color: white; }
    .step-label { font-size: 13px; color: var(--text-muted); font-weight: 500; }
    .step.active .step-label { color: var(--primary); font-weight: 600; }

    /* Cards */
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 32px; box-shadow: var(--shadow); margin-bottom: 24px; }
    .card h3 { margin: 0 0 16px 0; font-size: 20px; display: flex; align-items: center; gap: 12px; }
    .card h3 .icon { font-size: 24px; }
    
    /* Form Elements */
    .input-group { display: flex; gap: 12px; margin-bottom: 16px; }
    input[type="text"] { flex: 1; padding: 12px 16px; border: 1px solid var(--border); border-radius: 8px; font-size: 15px; outline: none; transition: border 0.2s; }
    input[type="text"]:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
    select { padding: 12px 16px; border: 1px solid var(--border); border-radius: 8px; font-size: 15px; background: white; }
    
    button { padding: 12px 24px; border-radius: 8px; border: none; font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
    .btn-primary { background: var(--primary); color: white; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-primary:disabled { background: #94a3b8; cursor: not-allowed; }
    .btn-danger { background: white; color: var(--danger); border: 1px solid var(--danger); }
    .btn-danger:hover { background: #fef2f2; }
    .btn-secondary { background: #f1f5f9; color: var(--text-main); border: 1px solid var(--border); }

    /* Status & Logs */
    .status-box { padding: 16px; border-radius: 8px; margin-bottom: 16px; font-weight: 500; }
    .status-idle { background: #f1f5f9; color: var(--text-muted); }
    .status-running { background: #eff6ff; color: var(--primary); }
    .status-done { background: #dcfce7; color: #166534; }
    .status-error { background: #fef2f2; color: #991b1b; }
    
    .log-terminal { background: #0f172a; color: #a7f3d0; font-family: 'Consolas', monospace; font-size: 13px; padding: 16px; border-radius: 8px; height: 200px; overflow-y: auto; white-space: pre-wrap; margin-top: 16px; }
    
    .progress-bar { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; margin: 12px 0; }
    .progress-fill { height: 100%; background: var(--primary); width: 0%; transition: width 0.3s; }
    .progress-fill.indeterminate { width: 30%; animation: indeterminate 1.5s infinite linear; }
    @keyframes indeterminate { 0% { transform: translateX(-100%); } 100% { transform: translateX(400%); } }

    .hint { font-size: 13px; color: var(--text-muted); margin-top: 8px; }
  </style>
</head>
<body>
  <div class="topbar">
    <h2>▶ 普通玩家模式</h2>
    <div style="display:flex; gap:12px; align-items:center;">
      <button class="btn-primary" style="padding:8px 16px; font-size:13px;" onclick="chooseFolder()">选择游戏文件夹</button>
      <span id="folder_status" class="hint" style="margin:0;"></span>
      <a href="__PORTAL_URL__" class="back-btn">← 返回首页</a>
    </div>
  </div>

  <div class="container">
    <!-- Steps Indicator -->
    <div class="steps">
      <div class="step active" id="step1">
        <div class="step-num">1</div>
        <div class="step-label">选择游戏</div>
      </div>
      <div class="step" id="step2">
        <div class="step-num">2</div>
        <div class="step-label">自动翻译</div>
      </div>
      <div class="step" id="step3">
        <div class="step-num">3</div>
        <div class="step-label">应用结果</div>
      </div>
    </div>

    <!-- Step 1: Select Game -->
    <div class="card" id="card1">
      <h3><span class="icon">⌂</span> 选择游戏文件夹</h3>
      <p style="color:var(--text-muted); margin-bottom:20px;">选择包含游戏资源文件的文件夹，建议使用游戏安装目录。</p>
      <div class="input-group">
        <input type="text" id="game_path" placeholder="尚未选择文件夹，例如 D:\\Games\\MyRPGGame">
        <select id="target_language">
          <option value="zh-CN">简体中文</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </select>
      </div>
      <button class="btn-primary" id="start_btn" onclick="startTranslation()">开始翻译 →</button>
      <div class="hint">注意：请选择游戏文件夹，不要选择 .exe 文件。</div>
    </div>

    <!-- Step 2: Translation Status -->
    <div class="card" id="card2" style="display:none;">
      <h3><span class="icon">✦</span> 自动翻译中</h3>
      <div class="status-box status-running" id="status_text">等待开始...</div>
      <div class="progress-bar"><div class="progress-fill indeterminate" id="progress_fill"></div></div>
      <div class="log-terminal" id="log"></div>
    </div>

    <!-- Step 3: Apply -->
    <div class="card" id="card3" style="display:none;">
      <h3><span class="icon">✓</span> 翻译完成</h3>
      <p style="color:var(--text-muted); margin-bottom:24px;">补丁已生成。你可以将修改应用到游戏，系统会自动备份原始文件。</p>
      <div style="display:flex; gap:12px;">
        <button class="btn-primary" id="apply_btn" onclick="applyPatch()">应用到游戏</button>
        <button class="btn-danger" id="restore_btn" onclick="restore()">恢复原始文件</button>
      </div>
      <div class="hint">安全提示：应用或恢复都会先询问一次，避免误修改游戏文件。</div>
    </div>
  </div>

  <script>
    const PORTAL_URL = "__PORTAL_URL__";
    let polling = false;

    function showStep(n) {
      document.getElementById('card1').style.display = n === 1 ? 'block' : 'none';
      document.getElementById('card2').style.display = n === 2 ? 'block' : 'none';
      document.getElementById('card3').style.display = n === 3 ? 'block' : 'none';
      
      for(let i=1; i<=3; i++) {
        const el = document.getElementById('step' + i);
        el.classList.remove('active', 'done');
        if (i < n) el.classList.add('done');
        if (i === n) el.classList.add('active');
      }
    }

    async function chooseFolder() {
    const statusEl = document.getElementById('folder_status');
    try {
        statusEl.textContent = '正在打开文件夹选择窗口...';
        const res = await fetch('/api/select_folder', { method: 'POST' });
        const data = await res.json();
        if (!data.path) { statusEl.textContent = '未选择文件夹。'; return; }
        document.getElementById('game_path').value = data.path;
        statusEl.textContent = '已选择：' + data.path;
    } catch (err) {
        statusEl.textContent = '选择失败：' + err;
    }
    }

    async function api(url, method, body) {
      const opts = { method: method || "GET", headers: {} };
      if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
      const res = await fetch(url, opts);
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      return res.json();
    }

    async function startTranslation() {
      const path = document.getElementById('game_path').value.trim();
      const lang = document.getElementById('target_language').value;
      if (!path) { alert("请先输入或选择游戏文件夹路径。"); return; }
      
      document.getElementById('start_btn').disabled = true;
      showStep(2);
      
      try {
        await api("/api/start", "POST", { game_path: path, target_language: lang });
        startPolling();
      } catch (err) {
        alert("启动失败: " + err);
        showStep(1);
        document.getElementById('start_btn').disabled = false;
      }
    }

    async function applyPatch() {
      if (!confirm("确定要应用到游戏吗？\\n系统会自动备份原文件。")) return;
      try {
        await api("/api/apply", "POST");
        alert("应用成功！");
      } catch (err) { alert("应用失败: " + err); }
    }

    async function restore() {
      if (!confirm("确定要恢复原始文件吗？")) return;
      try {
        await api("/api/restore", "POST");
        alert("恢复成功！");
      } catch (err) { alert("恢复失败: " + err); }
    }

    function startPolling() {
      if (polling) return;
      polling = true;
      pollState();
    }

    async function pollState() {
      try {
        const state = await api("/api/state");
        document.getElementById('status_text').innerText = state.step + ": " + state.message;
        document.getElementById('log').innerText = state.logs.join("\\n");
        document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;

        if (state.state === 'done' || state.state === 'applied' || state.state === 'restored') {
          showStep(3);
          polling = false;
        } else if (state.state === 'error') {
          document.getElementById('status_text').className = 'status-box status-error';
          polling = false;
          showStep(1);
          document.getElementById('start_btn').disabled = false;
        } else {
          setTimeout(pollState, 1500);
        }
      } catch (err) {
        console.error(err);
        polling = false;
      }
    }
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
        portal_url = os.environ.get("AGL_PORTAL_URL", "http://127.0.0.1:8300/")
        return PAGE.replace("__PORTAL_URL__", portal_url)

    @app.post("/api/select_folder")
    def api_select_folder():
        return {"path": select_folder_native()}

    from fastapi.responses import RedirectResponse

    @app.get("/desktop")
    def desktop_redirect():
        return RedirectResponse(url="/")

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