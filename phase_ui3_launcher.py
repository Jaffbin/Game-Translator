from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agl.user_settings import (
    load_user_settings,
    reset_ui_mode,
    save_user_settings,
    set_ui_mode,
)
from phase_ui1_simple import create_simple_app

try:
    import webview
except ImportError:
    webview = None


# ----------------------------------------------------------------------
# Network helpers
# ----------------------------------------------------------------------

def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start_port: int, max_attempts: int = 200) -> int:
    for port in range(start_port, start_port + max_attempts):
        if not is_port_open(port):
            return port

    raise RuntimeError(
        f"No free port found between {start_port} "
        f"and {start_port + max_attempts - 1}"
    )


def resolve_port(preferred_port: int) -> int:
    if not is_port_open(preferred_port):
        return preferred_port

    return find_free_port(preferred_port + 1)


def wait_for_port(port: int, timeout_seconds: float = 15.0) -> bool:
    start = time.time()

    while time.time() - start < timeout_seconds:
        if is_port_open(port):
            return True

        time.sleep(0.15)

    return False


# ----------------------------------------------------------------------
# Native folder dialog (no pywebview, no tkinter, thread-safe)
# ----------------------------------------------------------------------

def select_folder_native() -> str:
    """
    Open a native folder picker using PowerShell (Windows only).

    Runs in a subprocess, so it is safe to call from any thread.
    """
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


# ----------------------------------------------------------------------
# Portal page (first-run wizard + main menu)
# ----------------------------------------------------------------------

PORTAL_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer</title>
  <style>
    body {
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: linear-gradient(135deg, #eef2ff, #e2e8f0);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .container {
      width: 900px;
      max-width: calc(100vw - 40px);
      background: rgba(255,255,255,0.92);
      border: 1px solid #cbd5e1;
      border-radius: 18px;
      padding: 34px;
      box-shadow: 0 18px 50px rgba(15, 23, 42, 0.16);
    }

    h1 { margin: 0 0 8px 0; font-size: 34px; color: #0f172a; }

    p.sub { margin: 0 0 22px 0; color: #475569; font-size: 15px; }

    .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }

    .card {
      border: 1px solid #cbd5e1;
      border-radius: 14px;
      padding: 22px;
      background: #fff;
      cursor: pointer;
      transition: 0.15s ease;
    }

    .card:hover {
      transform: translateY(-2px);
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.10);
      border-color: #93c5fd;
    }

    .card h2 { margin: 0 0 10px 0; font-size: 21px; color: #111827; }
    .card p { margin: 0; color: #475569; font-size: 14px; line-height: 1.55; }

    .actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 20px; }

    button {
      border: 0;
      border-radius: 10px;
      padding: 11px 16px;
      font-size: 14px;
      cursor: pointer;
    }

    button.primary { background: #2563eb; color: #fff; }
    button.secondary { background: #475569; color: #fff; }
    button.ghost { background: #e2e8f0; color: #111827; }

    .small { margin-top: 18px; color: #64748b; font-size: 12px; }
    .hidden { display: none; }

    .badge {
      display: inline-block;
      margin-left: 8px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #dbeafe;
      color: #1d4ed8;
      font-size: 12px;
      vertical-align: middle;
    }

    #browser_warning {
      color: #b91c1c;
      font-weight: bold;
      margin-bottom: 16px;
      padding: 10px;
      background: #fee2e2;
      border-radius: 8px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>AutoGame Localizer</h1>
    <p class="sub">本地游戏翻译工具</p>

    <div id="browser_warning" class="hidden"></div>

    <div id="mode_select" class="hidden">
      <h2 style="margin:0 0 14px 0; font-size:22px;">请选择使用模式</h2>

      <div class="cards">
        <div class="card" onclick="chooseMode('simple')">
          <h2>我是普通玩家</h2>
          <p>
            简单模式。<br><br>
            只需要选择游戏、开始翻译、应用补丁。<br>
            不需要了解项目、QA、CSV、provider 等概念。
          </p>
        </div>

        <div class="card" onclick="chooseMode('advanced')">
          <h2>我是译者 / 开发者</h2>
          <p>
            开发者模式。<br><br>
            可以管理项目、审校文本、导入导出 CSV、运行 QA、
            查看补丁预览、配置翻译服务。
          </p>
        </div>
      </div>

      <div class="small">之后可以随时重新选择模式。</div>
    </div>

    <div id="home" class="hidden">
      <h2 style="margin:0 0 14px 0; font-size:22px;">
        主菜单
        <span id="current_mode" class="badge"></span>
      </h2>

      <div class="actions">
        <button class="primary" onclick="openMode('simple')">进入普通模式</button>
        <button class="secondary" onclick="openMode('advanced')">进入开发者模式</button>
        <button class="secondary" onclick="openMode('settings')">打开设置</button>
        <button class="ghost" onclick="resetMode()">重新选择模式</button>
      </div>

      <div class="small">
        普通模式适合一键翻译。开发者模式适合人工审校和社区翻译项目。
      </div>
    </div>
  </div>

  <script>
    function show(id) {
      document.getElementById("mode_select").classList.add("hidden");
      document.getElementById("home").classList.add("hidden");
      document.getElementById(id).classList.remove("hidden");
    }

    function showWarning(text) {
      const el = document.getElementById("browser_warning");
      el.textContent = text;
      el.classList.remove("hidden");
    }

    async function api(url, method, body) {
      const options = { method: method || "GET", headers: {} };

      if (body !== null && body !== undefined) {
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

    async function init() {
      try {
        const settings = await api("/api/settings");

        if (!settings.first_run_completed) {
          show("mode_select");
        } else {
          const modeText = settings.ui_mode === "simple"
            ? "普通模式"
            : settings.ui_mode === "advanced"
              ? "开发者模式"
              : "未选择";

          document.getElementById("current_mode").textContent = modeText;
          show("home");
        }
      } catch (err) {
        showWarning("加载设置失败: " + err);
      }
    }

    async function chooseMode(mode) {
      try {
        const data = await api("/api/choose_mode", "POST", { mode: mode });
        window.location.href = data.url;
      } catch (err) {
        alert("选择模式失败: " + err);
      }
    }

    async function openMode(mode) {
      try {
        const data = await api("/api/open_mode", "POST", { mode: mode });
        window.location.href = data.url;
      } catch (err) {
        alert("打开模式失败: " + err);
      }
    }

    async function resetMode() {
      try {
        await api("/api/reset_mode", "POST");
        window.location.href = "/?r=" + Date.now();
      } catch (err) {
        alert("重置失败: " + err);
      }
    }

    window.addEventListener("load", init);
  </script>
</body>
</html>
"""


# ----------------------------------------------------------------------
# Simple mode desktop page (toolbar + iframe)
# ----------------------------------------------------------------------

SIMPLE_DESKTOP_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer Simple Mode</title>
  <style>
    html, body {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      font-family: "Segoe UI", Arial, sans-serif;
    }

    #toolbar {
      height: 52px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 12px;
      background: #e5e7eb;
      border-bottom: 1px solid #cbd5e1;
      box-sizing: border-box;
    }

    #toolbar button {
      padding: 8px 14px;
      font-size: 14px;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
    }

    #chooseBtn { background: #2563eb; color: #fff; }
    #homeBtn { background: #475569; color: #fff; }

    #status {
      color: #475569;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 680px;
    }

    #frame {
      width: 100%;
      height: calc(100vh - 52px);
      border: 0;
      display: block;
    }
  </style>
</head>
<body>
  <div id="toolbar">
    <button id="chooseBtn" onclick="chooseFolder()">选择游戏文件夹</button>
    <button id="homeBtn" onclick="goHome()">返回首页</button>
    <div id="status">简单模式</div>
  </div>

  <iframe id="frame" src="/"></iframe>

  <script>
    const PORTAL_URL = "__PORTAL_URL__";

    function setStatus(message) {
      document.getElementById("status").textContent = message;
    }

    async function chooseFolder() {
      try {
        setStatus("正在打开文件夹选择窗口...");

        const response = await fetch("/api/select_folder", { method: "POST" });
        const data = await response.json();

        if (!data.path) {
          setStatus("未选择文件夹。");
          return;
        }

        const doc = document.getElementById("frame").contentDocument;

        if (!doc) {
          setStatus("无法访问内部页面。");
          return;
        }

        const input = doc.getElementById("game_path");

        if (!input) {
          setStatus("找不到游戏路径输入框。");
          return;
        }

        input.value = data.path;
        setStatus("已选择：" + data.path);

      } catch (err) {
        setStatus("选择文件夹失败：" + err);
      }
    }

    function goHome() {
      window.location.href = PORTAL_URL;
    }

    window.addEventListener("load", function () {
      setStatus("简单模式就绪。");
    });
  </script>
</body>
</html>
"""


# ----------------------------------------------------------------------
# Service manager
# ----------------------------------------------------------------------

MODE_DEFAULT_PORTS = {
    "simple": 8310,
    "advanced": 8320,
    "settings": 8330,
}


class ServiceManager:
    def __init__(self, portal_url: str):
        self.portal_url = portal_url
        self.services: Dict[str, Dict[str, Any]] = {}

    def _create_app(self, mode: str) -> Tuple[FastAPI, str]:
        if mode == "simple":
            app = create_simple_app()
            portal_url = self.portal_url

            @app.get("/desktop", response_class=HTMLResponse)
            def simple_desktop_page() -> str:
                return SIMPLE_DESKTOP_PAGE.replace("__PORTAL_URL__", portal_url)

            @app.post("/api/select_folder")
            def api_select_folder():
                return {"path": select_folder_native()}

            return app, "/desktop"

        if mode == "advanced":
            try:
                from phase11_workspace import create_workspace_app
            except ImportError as exc:
                raise RuntimeError(
                    "开发者模式需要 phase11_workspace.py。请先完成 Phase 11。"
                ) from exc

            return create_workspace_app(), "/"

        if mode == "settings":
            try:
                from phase12_settings import create_settings_app
            except ImportError as exc:
                raise RuntimeError(
                    "设置页面需要 phase12_settings.py。请先完成 Phase 12。"
                ) from exc

            return create_settings_app(), "/"

        raise ValueError(f"Unknown mode: {mode}")

    def start(self, mode: str) -> str:
        existing = self.services.get(mode)

        if existing is not None:
            server = existing.get("server")
            thread = existing.get("thread")

            if (
                server is not None
                and thread is not None
                and thread.is_alive()
                and not getattr(server, "should_exit", False)
            ):
                return existing["url"]

        app, suffix = self._create_app(mode)

        preferred_port = MODE_DEFAULT_PORTS.get(mode, 8400)
        port = resolve_port(preferred_port)

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )

        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        if not wait_for_port(port):
            server.should_exit = True
            raise RuntimeError(f"Failed to start service for mode: {mode}")

        url = f"http://127.0.0.1:{port}{suffix}"

        self.services[mode] = {
            "server": server,
            "thread": thread,
            "port": port,
            "url": url,
        }

        return url

    def stop_all(self) -> None:
        for info in self.services.values():
            server = info.get("server")

            if server is not None:
                server.should_exit = True

        for info in self.services.values():
            thread = info.get("thread")

            if thread is not None:
                thread.join(timeout=2)

        self.services.clear()


# ----------------------------------------------------------------------
# Portal app (HTTP API, no pywebview js_api)
# ----------------------------------------------------------------------

class ModeRequest(BaseModel):
    mode: str


def create_portal_app(services: ServiceManager) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Launcher")

    @app.get("/", response_class=HTMLResponse)
    def portal_page() -> str:
        return PORTAL_PAGE

    @app.get("/api/settings")
    def api_settings():
        return load_user_settings()

    @app.post("/api/choose_mode")
    def api_choose_mode(body: ModeRequest):
        if body.mode not in {"simple", "advanced"}:
            raise HTTPException(status_code=400, detail="Invalid mode.")

        set_ui_mode(body.mode)
        url = services.start(body.mode)

        return {"url": url}

    @app.post("/api/open_mode")
    def api_open_mode(body: ModeRequest):
        if body.mode not in {"simple", "advanced", "settings"}:
            raise HTTPException(status_code=400, detail="Invalid mode.")

        if body.mode in {"simple", "advanced"}:
            settings = load_user_settings()
            settings["ui_mode"] = body.mode
            settings["first_run_completed"] = True
            save_user_settings(settings)

        url = services.start(body.mode)

        return {"url": url}

    @app.post("/api/reset_mode")
    def api_reset_mode():
        reset_ui_mode()
        return {"ok": True}

    return app


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="UI Phase 3: unified launcher with mode selection"
    )

    parser.add_argument("--portal-port", type=int, default=8300)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Do not open pywebview window; use browser only",
    )

    args = parser.parse_args()

    portal_port = resolve_port(args.portal_port)
    portal_url = f"http://127.0.0.1:{portal_port}/"

    services = ServiceManager(portal_url)
    portal_app = create_portal_app(services)

    portal_config = uvicorn.Config(
        portal_app,
        host="127.0.0.1",
        port=portal_port,
        log_level="warning",
    )

    portal_server = uvicorn.Server(portal_config)

    portal_thread = threading.Thread(target=portal_server.run, daemon=True)
    portal_thread.start()

    if not wait_for_port(portal_port):
        portal_server.should_exit = True
        sys.exit("Portal server did not start in time.")

    print("Opening AutoGame Localizer Launcher")
    print(f"Portal: {portal_url}")

    use_window = (
        webview is not None
        and not args.no_window
    )

    if use_window:
        window = webview.create_window(
            title="AutoGame Localizer",
            url=portal_url,
            width=1220,
            height=860,
            min_size=(1000, 700),
        )

        webview.start(debug=args.debug)

        services.stop_all()
        portal_server.should_exit = True
        portal_thread.join(timeout=3)

    else:
        print("pywebview window disabled or unavailable.")
        print("Open the portal in your browser. Press Ctrl+C to stop.")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            services.stop_all()
            portal_server.should_exit = True
            portal_thread.join(timeout=3)


if __name__ == "__main__":
    main()