from __future__ import annotations

import argparse
import os
import socket
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
    raise RuntimeError(f"No free port found between {start_port} and {start_port + max_attempts - 1}")

def resolve_port(preferred_port: int) -> int:
    return preferred_port if not is_port_open(preferred_port) else find_free_port(preferred_port + 1)

def wait_for_port(port: int, timeout_seconds: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_seconds:
        if is_port_open(port):
            return True
        time.sleep(0.15)
    return False

# ----------------------------------------------------------------------
# Shared CSS & Portal HTML
# ----------------------------------------------------------------------
SHARED_CSS = """
:root {
  --bg: #f8fafc; --surface: #ffffff; --primary: #2563eb; --primary-hover: #1d4ed8;
  --text-main: #0f172a; --text-muted: #64748b; --border: #e2e8f0;
  --success: #10b981; --warning: #f59e0b; --danger: #ef4444;
  --radius: 12px; --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05);
}
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text-main); margin: 0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
.container { width: 860px; max-width: 92vw; }
h1 { font-size: 32px; margin: 0 0 8px 0; letter-spacing: -0.5px; }
.sub { color: var(--text-muted); margin: 0 0 32px 0; font-size: 16px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 12px; font-weight: 600; background: #dbeafe; color: var(--primary); vertical-align: middle; margin-left: 8px; }
.badge.green { background: #dcfce7; color: #166534; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; cursor: pointer; transition: all 0.2s; }
.card:hover { border-color: var(--primary); box-shadow: var(--shadow); transform: translateY(-2px); }
.card h3 { margin: 0 0 12px 0; font-size: 20px; display: flex; align-items: center; gap: 8px; }
.card p { margin: 0 0 12px 0; color: var(--text-muted); font-size: 14px; line-height: 1.6; }
.card ul { margin: 0; padding-left: 18px; color: var(--text-muted); font-size: 13px; line-height: 1.8; }
.menu-row { display: flex; align-items: center; gap: 14px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; margin-bottom: 12px; cursor: pointer; transition: all 0.15s; }
.menu-row:hover { border-color: var(--primary); box-shadow: var(--shadow); }
.menu-row .icon { font-size: 20px; }
.menu-row .title { font-weight: 600; font-size: 15px; }
.menu-row .desc { color: var(--text-muted); font-size: 13px; }
.actions { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
button { padding: 10px 20px; border-radius: 8px; border: 1px solid var(--border); background: var(--surface); font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s; }
button:hover { background: #f1f5f9; }
button.primary { background: var(--primary); color: white; border-color: var(--primary); }
button.primary:hover { background: var(--primary-hover); }
.muted { font-size: 13px; color: var(--text-muted); }
.hidden { display: none; }
"""

PORTAL_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AutoGame Localizer</title>
<style>__CSS__</style></head>
<body>
  <div class="container">
    <h1>AutoGame Localizer <span class="badge">DESKTOP WEB</span></h1>
    <p class="sub">让游戏本地化，变得简单。</p>
    <div id="mode_select" class="hidden">
      <div class="cards">
        <div class="card" onclick="chooseMode('simple')">
          <h3>▶ 普通玩家模式 <span class="badge green">推荐</span></h3>
          <p>隐藏技术细节，只保留“选择 → 翻译 → 应用”这条清晰路径。</p>
          <ul><li>✓ 选择游戏文件夹</li><li>✓ 查看翻译进度与日志</li><li>✓ 应用或恢复原始文件</li></ul>
        </div>
        <div class="card" onclick="chooseMode('advanced')">
          <h3>⌘ 译者 · 开发者模式</h3>
          <p>提供完整项目工作区、扫描、QA、Patch、Install、Rollback 与条目审校能力。</p>
          <ul><li>✓ 项目与 Provider 管理</li><li>✓ 条目级审校与锁定</li><li>✓ Patch 预览与任务日志</li></ul>
        </div>
      </div>
      <div class="actions">
        <button class="primary" onclick="chooseMode('simple')">进入普通模式 →</button>
        <button onclick="chooseMode('advanced')">进入开发者工作区</button>
      </div>
    </div>
    <div id="home" class="hidden">
      <div class="menu-row" onclick="openMode('simple')">
        <span class="icon">▣</span>
        <span><span class="title">开始工作</span><br><span class="desc">选择游戏并开始翻译</span></span>
        <span class="badge" id="current_mode" style="margin-left:auto;"></span>
      </div>
      <div class="menu-row" onclick="openMode('advanced')">
        <span class="icon">◫</span>
        <span><span class="title">项目</span><br><span class="desc">管理本地化项目与工作区</span></span>
      </div>
      <div class="menu-row" onclick="openMode('settings')">
        <span class="icon">⚙</span>
        <span><span class="title">设置</span><br><span class="desc">通用选项与 Provider API Key</span></span>
      </div>
      <div class="actions">
        <button onclick="resetMode()">重新选择模式</button>
      </div>
    </div>
  </div>
<script>
function show(id) { document.getElementById("mode_select").classList.add("hidden"); document.getElementById("home").classList.add("hidden"); document.getElementById(id).classList.remove("hidden"); }
async function api(url, method, body) {
  const opts = { method: method || "GET", headers: {} };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
async function init() {
  try {
    const s = await api("/api/settings");
    if (!s.first_run_completed) { show("mode_select"); }
    else {
      document.getElementById("current_mode").textContent = s.ui_mode === "simple" ? "普通模式" : s.ui_mode === "advanced" ? "开发者模式" : "";
      show("home");
    }
  } catch (err) { console.error(err); }
}
async function chooseMode(mode) { try { window.location.href = (await api("/api/choose_mode", "POST", { mode: mode })).url; } catch (err) { alert(err); } }
async function openMode(mode) { try { window.location.href = (await api("/api/open_mode", "POST", { mode: mode })).url; } catch (err) { alert(err); } }
async function resetMode() { try { await api("/api/reset_mode", "POST"); window.location.href = "/?r=" + Date.now(); } catch (err) { alert(err); } }
window.addEventListener("load", init);
</script>
</body></html>
""".replace("__CSS__", SHARED_CSS)

# ----------------------------------------------------------------------
# Service manager
# ----------------------------------------------------------------------
MODE_DEFAULT_PORTS = {"simple": 8310, "advanced": 8320, "settings": 8330}

class ServiceManager:
    def __init__(self, portal_url: str):
        self.portal_url = portal_url
        self.services: Dict[str, Dict[str, Any]] = {}

    def _create_app(self, mode: str) -> Tuple[FastAPI, str]:
        # 将 portal_url 注入到环境变量，供子应用读取
        os.environ["AGL_PORTAL_URL"] = self.portal_url

        if mode == "simple":
            return create_simple_app(), "/"

        if mode == "advanced":
            try:
                from phase11_workspace import create_workspace_app
            except ImportError as exc:
                raise RuntimeError("开发者模式需要 phase11_workspace.py。") from exc
            return create_workspace_app(), "/"

        if mode == "settings":
            try:
                from phase12_settings import create_settings_app
            except ImportError as exc:
                raise RuntimeError("设置页面需要 phase12_settings.py。") from exc
            return create_settings_app(), "/"

        raise ValueError(f"Unknown mode: {mode}")

    def start(self, mode: str) -> str:
        existing = self.services.get(mode)
        if existing is not None:
            server = existing.get("server")
            thread = existing.get("thread")
            if server and thread and thread.is_alive() and not getattr(server, "should_exit", False):
                return existing["url"]

        app, suffix = self._create_app(mode)
        port = resolve_port(MODE_DEFAULT_PORTS.get(mode, 8400))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        if not wait_for_port(port):
            server.should_exit = True
            raise RuntimeError(f"Failed to start service for mode: {mode}")

        url = f"http://127.0.0.1:{port}{suffix}"
        self.services[mode] = {"server": server, "thread": thread, "port": port, "url": url}
        return url

    def stop_all(self) -> None:
        for info in self.services.values():
            if info.get("server"): info["server"].should_exit = True
        for info in self.services.values():
            if info.get("thread"): info["thread"].join(timeout=2)
        self.services.clear()

# ----------------------------------------------------------------------
# Portal app
# ----------------------------------------------------------------------
class ModeRequest(BaseModel):
    mode: str

def create_portal_app(services: ServiceManager) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Launcher")

    @app.get("/", response_class=HTMLResponse)
    def portal_page() -> str:
        return PORTAL_PAGE

    @app.get("/api/settings")
    def api_settings(): return load_user_settings()

    @app.post("/api/choose_mode")
    def api_choose_mode(body: ModeRequest):
        if body.mode not in {"simple", "advanced"}: raise HTTPException(400, "Invalid mode.")
        set_ui_mode(body.mode)
        return {"url": services.start(body.mode)}

    @app.post("/api/open_mode")
    def api_open_mode(body: ModeRequest):
        if body.mode not in {"simple", "advanced", "settings"}: raise HTTPException(400, "Invalid mode.")
        if body.mode in {"simple", "advanced"}:
            s = load_user_settings()
            s["ui_mode"] = body.mode
            s["first_run_completed"] = True
            save_user_settings(s)
        return {"url": services.start(body.mode)}

    @app.post("/api/reset_mode")
    def api_reset_mode():
        reset_ui_mode()
        return {"ok": True}

    return app

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="UI Phase 3 launcher")
    parser.add_argument("--portal-port", type=int, default=8300)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    args = parser.parse_args()

    portal_port = resolve_port(args.portal_port)
    portal_url = f"http://127.0.0.1:{portal_port}/"

    services = ServiceManager(portal_url)
    portal_server = uvicorn.Server(uvicorn.Config(create_portal_app(services), host="127.0.0.1", port=portal_port, log_level="warning"))
    portal_thread = threading.Thread(target=portal_server.run, daemon=True)
    portal_thread.start()

    if not wait_for_port(portal_port):
        portal_server.should_exit = True
        sys.exit("Portal server did not start in time.")

    print(f"Portal: {portal_url}")

    if webview is not None and not args.no_window:
        webview.create_window(title="AutoGame Localizer", url=portal_url, width=1220, height=860, min_size=(1000, 700))
        webview.start(debug=args.debug)
        services.stop_all()
        portal_server.should_exit = True
        portal_thread.join(timeout=3)
    else:
        print("Browser mode. Ctrl+C to stop.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            services.stop_all()
            portal_server.should_exit = True
            portal_thread.join(timeout=3)

if __name__ == "__main__":
    main()