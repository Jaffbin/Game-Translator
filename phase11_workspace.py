from __future__ import annotations

import os
import argparse
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from agl import workspace_projects
from agl.workspace import ensure_workspace

from phase75_web import create_app as create_console_app


# ----------------------------------------------------------------------
# Network helpers
# ----------------------------------------------------------------------

def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start_port: int = 8001, max_attempts: int = 200) -> int:
    for port in range(start_port, start_port + max_attempts):
        if not is_port_open(port):
            return port

    raise RuntimeError(
        f"No free port found between {start_port} "
        f"and {start_port + max_attempts - 1}"
    )


def wait_for_port(port: int, timeout_seconds: float = 10.0) -> bool:
    start = time.time()

    while time.time() - start < timeout_seconds:
        if is_port_open(port):
            return True

        time.sleep(0.15)

    return False


# ----------------------------------------------------------------------
# Console manager
# ----------------------------------------------------------------------

class ConsoleManager:
    """
    Manages one Phase 7.5 console per project.
    """

    def __init__(self):
        self.consoles: Dict[str, Dict[str, Any]] = {}

    def start(self, project_name: str, project_dir: Path | str) -> int:
        project_dir = Path(project_dir).resolve()

        existing = self.consoles.get(project_name)

        if existing is not None:
            server = existing.get("server")
            thread = existing.get("thread")

            if (
                server is not None
                and thread is not None
                and thread.is_alive()
                and not getattr(server, "should_exit", False)
            ):
                return existing["port"]

        port = find_free_port()

        app = create_console_app(project_dir)

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )

        server = uvicorn.Server(config)

        thread = threading.Thread(
            target=server.run,
            daemon=True,
        )

        self.consoles[project_name] = {
            "server": server,
            "thread": thread,
            "port": port,
            "project_dir": str(project_dir),
        }

        thread.start()

        if not wait_for_port(port):
            raise RuntimeError(
                f"Failed to start console for project: {project_name}"
            )

        return port

    def list(self) -> list[Dict[str, Any]]:
        items = []

        for project_name, info in self.consoles.items():
            server = info.get("server")
            thread = info.get("thread")

            running = (
                server is not None
                and thread is not None
                and thread.is_alive()
                and not getattr(server, "should_exit", False)
            )

            items.append(
                {
                    "project": project_name,
                    "port": info.get("port"),
                    "running": running,
                }
            )

        return items

    def stop_all(self) -> None:
        for info in self.consoles.values():
            server = info.get("server")

            if server is not None:
                server.should_exit = True

        for info in self.consoles.values():
            thread = info.get("thread")

            if thread is not None:
                thread.join(timeout=2)

        self.consoles.clear()


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str
    game_path: str
    target_language: str = "zh-CN"


class ProjectNameRequest(BaseModel):
    name: str


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Developer Workspace</title>
<style>
:root {
  --bg: #f8fafc; --surface: #ffffff; --primary: #2563eb; --primary-hover: #1d4ed8;
  --text-main: #0f172a; --text-muted: #64748b; --border: #e2e8f0;
  --success: #10b981; --warning: #f59e0b; --danger: #ef4444;
  --radius: 12px; --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text-main); display: flex; min-height: 100vh; }
.sidebar { width: 220px; background: #0f172a; color: #cbd5e1; padding: 20px 12px; flex-shrink: 0; }
.sidebar .logo { color: #fff; font-weight: 700; font-size: 15px; padding: 0 10px 18px 10px; }
.sidebar a { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-radius: 8px; color: #cbd5e1; text-decoration: none; font-size: 14px; margin-bottom: 4px; }
.sidebar a.active, .sidebar a:hover { background: #1e293b; color: #fff; }
.sidebar a.home { margin-top: 24px; color: #94a3b8; }
.main { flex: 1; padding: 28px 32px; overflow: auto; }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.head h1 { margin: 0; font-size: 24px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 12px; font-weight: 600; }
.badge.green { background: #dcfce7; color: #166534; }
.badge.gray { background: #e2e8f0; color: #475569; }
.badge.amber { background: #fef3c7; color: #92400e; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px 24px; margin-bottom: 18px; box-shadow: var(--shadow); }
.card h3 { margin: 0 0 14px 0; font-size: 16px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
input, select { padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; outline: none; background: #fff; }
input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
button { padding: 10px 18px; border-radius: 8px; border: 1px solid var(--border); background: #fff; font-size: 14px; font-weight: 500; cursor: pointer; }
button:hover { background: #f1f5f9; }
button.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
button.primary:hover { background: var(--primary-hover); }
button.small { padding: 6px 12px; font-size: 13px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; padding: 10px 12px; border-bottom: 1px solid var(--border); }
td { padding: 14px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:hover td { background: #f8fafc; }
.muted { color: var(--text-muted); font-size: 13px; }
</style>
</head>
<body>
  <div class="sidebar">
    <div class="logo">⌘ Developer Workspace</div>
    <a href="#" class="active">◫ 项目</a>
    <a href="__PORTAL_URL__">▤ Project Console</a>
    <a href="__PORTAL_URL__">⚙ Settings</a>
    <a href="__PORTAL_URL__" class="home">← 返回首页</a>
  </div>

  <div class="main">
    <div class="head">
      <h1>项目工作区</h1>
      <span class="muted" id="count"></span>
    </div>

    <div class="card">
      <h3>创建项目</h3>
      <div class="row">
        <input id="new_name" placeholder="Project Name" style="width:180px;">
        <input id="new_game_path" placeholder="Game Folder, e.g. D:\\Games\\MyGame" style="flex:1;min-width:260px;">
        <select id="new_lang">
          <option value="zh-CN">简体中文</option>
          <option value="zh-TW">繁体中文</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </select>
        <button class="primary" onclick="createProject()">Create Project</button>
      </div>
      <p class="muted" style="margin:10px 0 0 0;">支持引擎：RPG Maker MV/MZ、Ren'Py 翻译模板、Unity 轻量文本。</p>
    </div>

    <div class="card">
      <h3>导入已有项目</h3>
      <div class="row">
        <input type="file" id="import_file" accept=".zip">
        <button onclick="importProject()">Import ZIP</button>
        <span class="muted">支持 ZIP 项目包，导入后会出现在项目表中。</span>
      </div>
    </div>

    <div class="card">
      <h3>Projects</h3>
      <table>
        <thead>
          <tr><th>Project</th><th>Engine</th><th>Target</th><th>Updated</th><th>Status</th><th>Actions</th></tr>
        </thead>
        <tbody id="projects"></tbody>
      </table>
    </div>
  </div>

<script>
function badgeFor(p) {
  if ((p.patches || 0) > 0) return '<span class="badge green">Ready</span>';
  if ((p.backups || 0) > 0) return '<span class="badge amber">Has Backup</span>';
  return '<span class="badge gray">Draft</span>';
}
async function api(url, method, body, isForm) {
  const opts = { method: method || "GET", headers: {} };
  if (body && !isForm) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  if (body && isForm) { opts.body = body; }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
async function refreshProjects() {
  const data = await api("/api/projects");
  const items = data.items || [];
  document.getElementById("count").textContent = items.length + " 个项目";
  const tbody = document.getElementById("projects");
  tbody.innerHTML = "";
  items.forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      "<td><b>" + p.name + "</b><br><span class='muted'>projects/" + p.name + "</span></td>" +
      "<td>" + p.engine + "</td>" +
      "<td>" + p.target_language + "</td>" +
      "<td class='muted'>" + (p.updated_at || "").slice(0, 16).replace("T", " ") + "</td>" +
      "<td>" + badgeFor(p) + "</td>";
    const td = document.createElement("td");
    const b1 = document.createElement("button"); b1.className = "small"; b1.textContent = "Open Console"; b1.onclick = () => openConsole(p.name);
    const b2 = document.createElement("button"); b2.className = "small"; b2.textContent = "Export"; b2.onclick = () => exportProject(p.name);
    const b3 = document.createElement("button"); b3.className = "small"; b3.textContent = "Archive"; b3.onclick = () => archiveProject(p.name);
    td.appendChild(b1); td.appendChild(document.createTextNode(" ")); td.appendChild(b2); td.appendChild(document.createTextNode(" ")); td.appendChild(b3);
    tr.appendChild(td);
    tbody.appendChild(tr);
  });
}
async function createProject() {
  const name = document.getElementById("new_name").value.trim();
  const gamePath = document.getElementById("new_game_path").value.trim();
  const lang = document.getElementById("new_lang").value;
  if (!name || !gamePath) { alert("Project name 与 Game Folder 必填。"); return; }
  try {
    await api("/api/projects/create", "POST", { name: name, game_path: gamePath, target_language: lang });
    document.getElementById("new_name").value = "";
    document.getElementById("new_game_path").value = "";
    refreshProjects();
  } catch (err) { alert("创建失败: " + err); }
}
async function openConsole(name) {
  try {
    const data = await api("/api/projects/open", "POST", { name: name });
    window.open(data.url, "_blank");
  } catch (err) { alert("打开失败: " + err); }
}
function exportProject(name) { window.location.href = "/api/projects/export?name=" + encodeURIComponent(name); }
async function archiveProject(name) {
  if (!confirm("归档项目 " + name + "？不会永久删除。")) return;
  try { await api("/api/projects/archive", "POST", { name: name }); refreshProjects(); }
  catch (err) { alert("归档失败: " + err); }
}
async function importProject() {
  const input = document.getElementById("import_file");
  if (!input.files.length) { alert("请先选择 ZIP 文件。"); return; }
  const fd = new FormData();
  fd.append("file", input.files[0]);
  try { await api("/api/projects/import", "POST", fd, true); refreshProjects(); }
  catch (err) { alert("导入失败: " + err); }
}
refreshProjects();
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------
# App
# ----------------------------------------------------------------------

def create_workspace_app() -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Workspace")

    console_manager = ConsoleManager()

    @app.on_event("shutdown")
    def shutdown() -> None:
        console_manager.stop_all()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        portal_url = os.environ.get("AGL_PORTAL_URL", "http://127.0.0.1:8300/")
        return PAGE.replace("__PORTAL_URL__", portal_url)

    @app.get("/api/projects")
    def api_projects():
        return {
            "items": workspace_projects.list_projects(),
        }

    @app.post("/api/projects/create")
    def api_projects_create(body: ProjectCreateRequest):
        try:
            result = workspace_projects.create_project(
                name=body.name,
                game_path=body.game_path,
                target_language=body.target_language,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return result

    @app.post("/api/projects/open")
    def api_projects_open(body: ProjectNameRequest):
        projects = {
            item["name"]: item
            for item in workspace_projects.list_projects()
        }

        project = projects.get(body.name)

        if project is None:
            raise HTTPException(
                status_code=404,
                detail=f"Project not found: {body.name}",
            )

        try:
            port = console_manager.start(
                project_name=body.name,
                project_dir=project["path"],
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {
            "name": body.name,
            "port": port,
            "url": f"http://127.0.0.1:{port}",
        }

    @app.post("/api/projects/archive")
    def api_projects_archive(body: ProjectNameRequest):
        try:
            result = workspace_projects.archive_project(body.name)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return result

    @app.get("/api/projects/export")
    def api_projects_export(
        name: str = Query(...),
    ):
        try:
            zip_path = workspace_projects.export_project_zip(name)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return FileResponse(
            path=str(zip_path),
            media_type="application/zip",
            filename=zip_path.name,
        )

    @app.post("/api/projects/import")
    async def api_projects_import(
        file: UploadFile = File(...),
        target_name: Optional[str] = Form(None),
    ):
        import shutil
        import tempfile

        tmp_path: Optional[Path] = None

        try:
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".zip",
            ) as tmp:
                shutil.copyfileobj(file.file, tmp)
                tmp_path = Path(tmp.name)

            result = workspace_projects.import_project_zip(
                zip_path=tmp_path,
                target_name=target_name,
            )

            return result

        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    @app.get("/api/consoles")
    def api_consoles():
        return {
            "items": console_manager.list(),
        }

    return app


# ----------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 11: workspace web home"
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
    )

    args = parser.parse_args()

    ensure_workspace()

    app = create_workspace_app()

    print("Opening AutoGame Localizer Workspace")
    print(f"Visit: http://{args.host}:{args.port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()