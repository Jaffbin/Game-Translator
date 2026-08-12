from __future__ import annotations

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
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer Workspace</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 16px;
      background: #f7f7f7;
    }

    h1 {
      margin: 0 0 12px 0;
      font-size: 22px;
    }

    h2 {
      font-size: 16px;
      margin: 0 0 8px 0;
    }

    .panel {
      background: #fff;
      border: 1px solid #ddd;
      padding: 10px;
      margin-bottom: 12px;
    }

    input[type=text] {
      padding: 6px;
      margin-right: 6px;
    }

    button {
      padding: 6px 10px;
      cursor: pointer;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
    }

    th, td {
      border: 1px solid #ddd;
      padding: 6px;
      font-size: 13px;
      vertical-align: top;
    }

    th {
      background: #efefef;
      text-align: left;
    }

    .ok {
      color: green;
      font-weight: bold;
    }

    .error {
      color: red;
      font-weight: bold;
    }

    .small {
      color: #666;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <h1>
    AutoGame Localizer Workspace
    <a href="http://127.0.0.1:8100" target="_blank" style="font-size:14px; margin-left:12px;">
        Settings
    </a>
  </h1>

  <div class="panel">
    <h2>Create Project</h2>
    <div>
      <input id="new_name" type="text" placeholder="Project name">
      <input id="new_game_path" type="text" placeholder="Game path, e.g. D:\\Games\\MyGame" style="width:360px;">
      <input id="new_lang" type="text" value="zh-CN" style="width:80px;">
      <button onclick="createProject()">Create</button>
      <span id="message"></span>
    </div>
    <div class="small">
      Supported engines: RPG Maker MV/MZ, Ren'Py translation templates, Unity lightweight text.
    </div>
  </div>

  <div class="panel">
    <h2>Import Project ZIP</h2>
    <input type="file" id="import_file" accept=".zip">
    <button onclick="importProject()">Import</button>
  </div>

  <div class="panel">
    <h2>Projects</h2>
    <table>
      <thead>
        <tr>
          <th>Project</th>
          <th>Engine</th>
          <th>Target</th>
          <th>Game Path</th>
          <th>Patches</th>
          <th>Backups</th>
          <th>Updated</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="projects"></tbody>
    </table>
  </div>

  <script>
    function setMessage(text, kind) {
      const el = document.getElementById("message");
      el.textContent = text || "";
      el.className = kind || "";
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

    async function refreshProjects() {
      const data = await api("/api/projects");
      const tbody = document.getElementById("projects");
      tbody.innerHTML = "";

      (data.items || []).forEach(project => {
        const tr = document.createElement("tr");

        const tdName = document.createElement("td");
        tdName.textContent = project.name;

        const tdEngine = document.createElement("td");
        tdEngine.textContent = project.engine;

        const tdTarget = document.createElement("td");
        tdTarget.textContent = project.target_language;

        const tdGamePath = document.createElement("td");
        tdGamePath.textContent = project.game_path;

        const tdPatches = document.createElement("td");
        tdPatches.textContent = project.patches;

        const tdBackups = document.createElement("td");
        tdBackups.textContent = project.backups;

        const tdUpdated = document.createElement("td");
        tdUpdated.textContent = project.updated_at || "";

        const tdActions = document.createElement("td");

        const openButton = document.createElement("button");
        openButton.textContent = "Open Console";
        openButton.onclick = () => openConsole(project.name);

        const exportButton = document.createElement("button");
        exportButton.textContent = "Export";
        exportButton.onclick = () => exportProject(project.name);

        const archiveButton = document.createElement("button");
        archiveButton.textContent = "Archive";
        archiveButton.onclick = () => archiveProject(project.name);

        tdActions.appendChild(openButton);
        tdActions.appendChild(document.createTextNode(" "));
        tdActions.appendChild(exportButton);
        tdActions.appendChild(document.createTextNode(" "));
        tdActions.appendChild(archiveButton);

        tr.appendChild(tdName);
        tr.appendChild(tdEngine);
        tr.appendChild(tdTarget);
        tr.appendChild(tdGamePath);
        tr.appendChild(tdPatches);
        tr.appendChild(tdBackups);
        tr.appendChild(tdUpdated);
        tr.appendChild(tdActions);

        tbody.appendChild(tr);
      });
    }

    async function createProject() {
      const name = document.getElementById("new_name").value.trim();
      const gamePath = document.getElementById("new_game_path").value.trim();
      const targetLanguage = document.getElementById("new_lang").value.trim() || "zh-CN";

      if (!name || !gamePath) {
        setMessage("Project name and game path are required.", "error");
        return;
      }

      try {
        setMessage("Creating project...", "");

        const data = await api("/api/projects/create", "POST", {
          name: name,
          game_path: gamePath,
          target_language: targetLanguage
        });

        setMessage("Project created: " + data.name, "ok");

        document.getElementById("new_name").value = "";
        document.getElementById("new_game_path").value = "";

        await refreshProjects();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    async function openConsole(name) {
      try {
        setMessage("Starting console for " + name + "...", "");

        const data = await api("/api/projects/open", "POST", {
          name: name
        });

        setMessage("Console running at " + data.url, "ok");

        window.open(data.url, "_blank");
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    function exportProject(name) {
      window.location.href = "/api/projects/export?name=" + encodeURIComponent(name);
    }

    async function archiveProject(name) {
      if (!confirm("Archive project: " + name + "?")) {
        return;
      }

      try {
        setMessage("Archiving project...", "");

        const data = await api("/api/projects/archive", "POST", {
          name: name
        });

        setMessage("Project archived.", "ok");

        await refreshProjects();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    async function importProject() {
      const input = document.getElementById("import_file");

      if (!input.files.length) {
        setMessage("Please choose a ZIP file first.", "error");
        return;
      }

      const formData = new FormData();
      formData.append("file", input.files[0]);

      try {
        setMessage("Importing project...", "");

        const response = await fetch("/api/projects/import", {
          method: "POST",
          body: formData
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || "Import failed");
        }

        setMessage("Project imported: " + data.name, "ok");

        input.value = "";

        await refreshProjects();
      } catch (err) {
        setMessage(err.message, "error");
      }
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
        return PAGE

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