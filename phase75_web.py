from __future__ import annotations

import shutil
import tempfile
import argparse
import sys
import threading
import traceback
import uuid
from datetime import datetime
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

from agl import operations
from agl.models import EntryStatus
from agl.project import ProjectStore


# ----------------------------------------------------------------------
# Background task manager
# ----------------------------------------------------------------------

class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def any_running(self) -> bool:
        with self.lock:
            return any(
                task["status"] == "running"
                for task in self.tasks.values()
            )

    def start(self, name: str, func) -> str:
        if self.any_running():
            raise RuntimeError(
                "Another task is already running. "
                "Please wait for it to finish."
            )

        task_id = uuid.uuid4().hex[:8]

        task = {
            "id": task_id,
            "name": name,
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "message": "",
            "result": None,
            "logs": [],
        }

        with self.lock:
            self.tasks[task_id] = task

        def log(message: str) -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            task["logs"].append(f"[{timestamp}] {message}")

        def worker() -> None:
            try:
                result = func(log)
                task["status"] = "completed"
                task["message"] = "OK"
                task["result"] = result
                log("Task completed.")
            except Exception as exc:
                task["status"] = "failed"
                task["message"] = str(exc)
                task["logs"].append("")
                task["logs"].append(traceback.format_exc())

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        return task_id

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def list(self) -> list[Dict[str, Any]]:
        items = []

        for task in sorted(
            self.tasks.values(),
            key=lambda x: x["created_at"],
            reverse=True,
        ):
            items.append(
                {
                    "id": task["id"],
                    "name": task["name"],
                    "status": task["status"],
                    "created_at": task["created_at"],
                    "message": task["message"],
                }
            )

        return items


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------

class EntryUpdate(BaseModel):
    entry_id: str
    target_text: Optional[str] = None
    status: Optional[str] = None
    locked: Optional[bool] = None
    ignored: Optional[bool] = None


class TranslateRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    retranslate: bool = False
    no_cache: bool = False


class QARequest(BaseModel):
    apply: bool = False
    apply_warnings: bool = False


class PatchRequest(BaseModel):
    patch_name: Optional[str] = None


class InstallRequest(BaseModel):
    patch_path: str
    force: bool = False
    backup: bool = True


class RollbackRequest(BaseModel):
    backup_id: Optional[str] = None
    force: bool = False


# ----------------------------------------------------------------------
# HTML page
# ----------------------------------------------------------------------

PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer Console</title>
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
      margin: 18px 0 8px 0;
    }

    .panel {
      background: #fff;
      border: 1px solid #ddd;
      padding: 10px;
      margin-bottom: 12px;
    }

    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
      align-items: center;
    }

    input[type=text], select {
      padding: 6px;
    }

    button {
      padding: 6px 10px;
      cursor: pointer;
    }

    #meta {
      white-space: pre-wrap;
      font-size: 13px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      table-layout: fixed;
    }

    th, td {
      border: 1px solid #ddd;
      padding: 6px;
      vertical-align: top;
      font-size: 13px;
      overflow-wrap: break-word;
    }

    th {
      background: #efefef;
      text-align: left;
    }

    td.source {
      width: 28%;
      white-space: pre-wrap;
    }

    td.target {
      width: 34%;
    }

    td.file {
      width: 16%;
      color: #555;
      font-size: 12px;
    }

    textarea.target {
      width: 100%;
      min-height: 58px;
      resize: vertical;
      box-sizing: border-box;
    }

    .status-cell {
      width: 120px;
    }

    .lock-cell {
      width: 50px;
      text-align: center;
    }

    .save-cell {
      width: 70px;
      text-align: center;
    }

    .pager {
      margin-top: 12px;
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .ok {
      color: green;
      font-weight: bold;
    }

    .error {
      color: red;
      font-weight: bold;
    }

    fieldset {
      border: 1px solid #ccc;
      margin-bottom: 10px;
    }

    legend {
      font-weight: bold;
      padding: 0 6px;
    }

    .actions-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 6px;
    }

    #tasklog {
      height: 220px;
      overflow: auto;
      background: #111;
      color: #9f9;
      font-family: Consolas, monospace;
      font-size: 12px;
      padding: 8px;
      white-space: pre-wrap;
    }

    #tasksTable td {
      cursor: pointer;
    }
  </style>
</head>
<body>
  <h1>AutoGame Localizer Console</h1>

  <div id="meta" class="panel">Loading project...</div>

  <div class="panel">
    <fieldset>
      <legend>Project Actions</legend>
      <div class="actions-row">
        <button onclick="startTask('/api/actions/scan', {})">Scan</button>
        <button onclick="startTask('/api/actions/qa', {apply:false, apply_warnings:false})">QA Check</button>
        <button onclick="startTask('/api/actions/qa', {apply:true, apply_warnings:true})">QA Apply</button>
        <button onclick="previewPatch()">Patch Preview</button>
        <button onclick="exportCsv()">Export CSV</button>
        <input type="file" id="csv_file" accept=".csv">
        <button onclick="importCsv()">Import CSV</button>
        <span id="message"></span>
      </div>
    </fieldset>

    <fieldset>
      <legend>Translate</legend>
      <div class="actions-row">
        <select id="provider">
          <option value="">default</option>
        </select>
        <input id="model" type="text" placeholder="model, empty=default">
        <label><input type="checkbox" id="retranslate"> retranslate</label>
        <label><input type="checkbox" id="no_cache"> no cache</label>
        <button onclick="startTranslate()">Translate</button>
      </div>
    </fieldset>

    <fieldset>
      <legend>Patch</legend>
      <div class="actions-row">
        <input id="patch_name" type="text" placeholder="patch name, empty=timestamp">
        <button onclick="startPatch()">Generate Patch</button>
      </div>
    </fieldset>

    <fieldset>
      <legend>Install / Rollback</legend>
      <div class="actions-row">
        <select id="patch_select"></select>
        <label><input type="checkbox" id="install_force"> force install</label>
        <button onclick="startInstall()">Install Patch</button>
      </div>
      <div class="actions-row">
        <select id="backup_select"></select>
        <button onclick="startRollback()">Rollback</button>
      </div>
    </fieldset>
  </div>

  <div class="panel">
    <h2>Patch Preview</h2>
    <pre id="patchpreview" style="height:180px;overflow:auto;background:#fff;border:1px solid #ddd;padding:8px;font-size:12px;"></pre>
  </div>

  <div class="panel">
    <h2>Tasks</h2>
    <table id="tasksTable">
      <thead>
        <tr>
          <th style="width:80px;">ID</th>
          <th style="width:120px;">Name</th>
          <th style="width:100px;">Status</th>
          <th style="width:160px;">Created</th>
          <th>Message</th>
        </tr>
      </thead>
      <tbody id="tasksBody"></tbody>
    </table>
    <div id="tasklog"></div>
  </div>

  <div class="panel">
    <h2>Entries</h2>

    <div class="toolbar">
      <input id="q" type="text" placeholder="Search source / target / file / context" style="min-width:280px;">
      <select id="status">
        <option value="">all statuses</option>
        <option value="pending">pending</option>
        <option value="machine_translated">machine_translated</option>
        <option value="needs_review">needs_review</option>
        <option value="reviewed">reviewed</option>
        <option value="locked">locked</option>
        <option value="ignored">ignored</option>
        <option value="error">error</option>
      </select>
      <button onclick="loadEntries()">Load</button>
    </div>

    <table>
      <thead>
        <tr>
          <th class="status-cell">Status</th>
          <th>Source</th>
          <th>Target</th>
          <th class="lock-cell">Lock</th>
          <th class="file-cell">File</th>
          <th class="save-cell">Save</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>

    <div class="pager">
      <button onclick="prevPage()">Prev</button>
      <span id="pageinfo"></span>
      <button onclick="nextPage()">Next</button>
    </div>
  </div>

  <script>
    let offset = 0;
    const limit = 100;
    let currentTaskId = null;

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

    async function refreshAll() {
      await loadMeta();
      await loadEntries();
      await refreshPatches();
      await refreshBackups();
    }

    async function loadMeta() {
      const data = await api("/api/meta");

      const meta = data.meta || {};
      const stats = data.stats || {};

      let lines = [];
      lines.push("Project: " + (meta.name || ""));
      lines.push("Engine: " + (meta.engine || ""));
      lines.push("Game path: " + (meta.game_path || ""));
      lines.push("Target language: " + (meta.target_language || ""));
      lines.push("");
      lines.push("Entry statuses:");

      for (const [key, value] of Object.entries(stats)) {
        lines.push("  " + key + ": " + value);
      }

      document.getElementById("meta").textContent = lines.join("\\n");
    }

    async function loadEntries() {
      const q = document.getElementById("q").value;
      const status = document.getElementById("status").value;

      const url = "/api/entries?q=" + encodeURIComponent(q) +
                  "&status=" + encodeURIComponent(status) +
                  "&limit=" + limit +
                  "&offset=" + offset;

      const data = await api(url);
      renderEntries(data.items || []);

      const total = data.total || 0;
      const start = total === 0 ? 0 : offset + 1;
      const end = Math.min(offset + limit, total);

      document.getElementById("pageinfo").textContent =
        "Showing " + start + "-" + end + " of " + total;
    }

    function renderEntries(items) {
      const tbody = document.getElementById("rows");
      tbody.innerHTML = "";

      const statusValues = [
        "pending",
        "machine_translated",
        "needs_review",
        "reviewed",
        "locked",
        "ignored",
        "error"
      ];

      items.forEach(entry => {
        const tr = document.createElement("tr");
        tr.dataset.id = entry.id;

        const tdStatus = document.createElement("td");
        tdStatus.className = "status-cell";

        const select = document.createElement("select");
        select.className = "status";

        statusValues.forEach(value => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = value;

          if (value === entry.status) {
            option.selected = true;
          }

          select.appendChild(option);
        });

        tdStatus.appendChild(select);

        const tdSource = document.createElement("td");
        tdSource.className = "source";
        tdSource.textContent = entry.source_text;

        const tdTarget = document.createElement("td");
        tdTarget.className = "target";

        const textarea = document.createElement("textarea");
        textarea.className = "target";
        textarea.value = entry.target_text || "";

        tdTarget.appendChild(textarea);

        const tdLocked = document.createElement("td");
        tdLocked.className = "lock-cell";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "locked";
        checkbox.checked = entry.locked;

        tdLocked.appendChild(checkbox);

        const tdFile = document.createElement("td");
        tdFile.className = "file";
        tdFile.textContent = entry.file_path;

        const tdSave = document.createElement("td");
        tdSave.className = "save-cell";

        const button = document.createElement("button");
        button.textContent = "Save";
        button.onclick = () => saveEntry(entry.id);

        tdSave.appendChild(button);

        tr.appendChild(tdStatus);
        tr.appendChild(tdSource);
        tr.appendChild(tdTarget);
        tr.appendChild(tdLocked);
        tr.appendChild(tdFile);
        tr.appendChild(tdSave);

        tbody.appendChild(tr);
      });
    }

    async function saveEntry(entryId) {
      const row = document.querySelector(`tr[data-id="${entryId}"]`);

      if (!row) {
        setMessage("Row not found.", "error");
        return;
      }

      const targetText = row.querySelector(".target").value;
      const status = row.querySelector(".status").value;
      const locked = row.querySelector(".locked").checked;

      const payload = {
        entry_id: entryId,
        target_text: targetText,
        status: status,
        locked: locked
      };

      try {
        const result = await api("/api/entries/update", "POST", payload);

        if (result.updated) {
          setMessage("Saved " + entryId.slice(0, 8), "ok");
        } else {
          setMessage("Save failed.", "error");
        }
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    async function startTask(url, body = {}) {
      try {
        setMessage("Starting...", "");
        const data = await api(url, "POST", body);

        currentTaskId = data.task_id;

        await refreshTasks();
        await pollCurrentTask();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    function startTranslate() {
      const body = {
        provider: document.getElementById("provider").value.trim() || null,
        model: document.getElementById("model").value.trim() || null,
        retranslate: document.getElementById("retranslate").checked,
        no_cache: document.getElementById("no_cache").checked
      };

      startTask("/api/actions/translate", body);
    }

    function startPatch() {
      const body = {
        patch_name: document.getElementById("patch_name").value.trim() || null
      };

      startTask("/api/actions/patch", body);
    }

    async function startInstall() {
      const patchPath = document.getElementById("patch_select").value;

      if (!patchPath) {
        alert("Please select a patch.");
        return;
      }

      if (!confirm("Install patch into game directory?")) {
        return;
      }

      const body = {
        patch_path: patchPath,
        force: document.getElementById("install_force").checked,
        backup: true
      };

      startTask("/api/actions/install", body);
    }

    async function startRollback() {
      const backupId = document.getElementById("backup_select").value || null;

      if (!confirm("Rollback game files?")) {
        return;
      }

      const body = {
        backup_id: backupId,
        force: false
      };

      startTask("/api/actions/rollback", body);
    }

    async function refreshTasks() {
      const data = await api("/api/tasks");
      const tbody = document.getElementById("tasksBody");
      tbody.innerHTML = "";

      (data.items || []).forEach(task => {
        const tr = document.createElement("tr");
        tr.onclick = () => viewTask(task.id);

        const tdId = document.createElement("td");
        tdId.textContent = task.id;

        const tdName = document.createElement("td");
        tdName.textContent = task.name;

        const tdStatus = document.createElement("td");
        tdStatus.textContent = task.status;

        const tdCreated = document.createElement("td");
        tdCreated.textContent = task.created_at;

        const tdMessage = document.createElement("td");
        tdMessage.textContent = task.message || "";

        tr.appendChild(tdId);
        tr.appendChild(tdName);
        tr.appendChild(tdStatus);
        tr.appendChild(tdCreated);
        tr.appendChild(tdMessage);

        tbody.appendChild(tr);
      });
    }

    async function viewTask(taskId) {
      currentTaskId = taskId;
      const task = await api(`/api/tasks/${taskId}`);
      document.getElementById("tasklog").textContent =
        (task.logs || []).join("\\n");
    }

    async function pollCurrentTask() {
      if (!currentTaskId) {
        return;
      }

      try {
        const task = await api(`/api/tasks/${currentTaskId}`);

        document.getElementById("tasklog").textContent =
          (task.logs || []).join("\\n");

        setMessage(
          `${task.name}: ${task.status} ${task.message || ""}`,
          task.status === "failed" ? "error" : "ok"
        );

        if (task.status === "running") {
          setTimeout(pollCurrentTask, 1500);
        } else {
          await refreshAll();
          await refreshTasks();
        }
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    async function refreshPatches() {
      const data = await api("/api/patches");
      const select = document.getElementById("patch_select");
      select.innerHTML = "";

      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "-- select patch --";
      select.appendChild(empty);

      (data.items || []).forEach(patch => {
        const option = document.createElement("option");
        option.value = patch.path;
        option.textContent = patch.name;
        select.appendChild(option);
      });
    }

    async function refreshBackups() {
      const data = await api("/api/backups");
      const select = document.getElementById("backup_select");
      select.innerHTML = "";

      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "-- latest backup --";
      select.appendChild(empty);

      (data.items || []).forEach(backup => {
        const option = document.createElement("option");
        option.value = backup.backup_id;
        option.textContent =
          backup.backup_id + "  " +
          (backup.created_at || "") + "  " +
          "files=" + (backup.files || []).length;
        select.appendChild(option);
      });
    }

    function prevPage() {
      offset = Math.max(0, offset - limit);
      loadEntries();
    }

    function nextPage() {
      offset += limit;
      loadEntries();
    }

    document.getElementById("q").addEventListener("keydown", event => {
      if (event.key === "Enter") {
        offset = 0;
        loadEntries();
      }
    });

    refreshProviders();
    refreshAll();
    refreshTasks();

    async function refreshProviders() {
      try {
        const data = await api("/api/config/providers");

        const select = document.getElementById("provider");

        if (!select) {
          return;
        }

        select.innerHTML = "";

        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "default";
        select.appendChild(empty);

        (data.items || []).forEach(p => {
          const option = document.createElement("option");
          option.value = p.id;

          let label = p.id + " | " + p.model;

          if (!p.has_api_key) {
            label += " | no key";
          }

          option.textContent = label;
          select.appendChild(option);
        });
      } catch (err) {
        console.error(err);
      }
    }

    function exportCsv() {
      const status = document.getElementById("status").value;
      const url = "/api/export/csv?status=" + encodeURIComponent(status || "");
      window.location.href = url;
    }

    async function importCsv() {
      const input = document.getElementById("csv_file");

      if (!input.files.length) {
        alert("Please choose a CSV file first.");
        return;
      }

      const formData = new FormData();
      formData.append("file", input.files[0]);
      formData.append("overwrite_locked", "false");

      try {
        setMessage("Importing CSV...", "");

        const response = await fetch("/api/import/csv", {
          method: "POST",
          body: formData
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || "Import failed");
        }

        setMessage(
          "CSV imported: " +
          "updated=" + (data.updated || 0) + ", " +
          "unchanged=" + (data.unchanged || 0) + ", " +
          "missing=" + (data.missing || 0) + ", " +
          "locked=" + (data.locked || 0),
          "ok"
        );

        await refreshAll();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    async function previewPatch() {
      try {
        setMessage("Generating patch preview...", "");

        const data = await api("/api/patch/preview");

        let lines = [];

        lines.push("Total entries: " + (data.total_entries || 0));
        lines.push("Patchable files: " + (data.patchable_files || 0));
        lines.push("Blocked QA error entries: " + (data.blocked_error_entries || 0));
        lines.push("");

        (data.files || []).forEach(f => {
          lines.push(f.file_path + "  entries=" + f.entry_count);

          (f.samples || []).forEach(s => {
            lines.push("    SRC: " + s.source);
            lines.push("    TGT: " + s.target);
          });

          lines.push("");
        });

        document.getElementById("patchpreview").textContent = lines.join("\\n");

        setMessage("Patch preview ready.", "ok");
      } catch (err) {
        setMessage(err.message, "error");
      }
    }
  </script>
</body>
</html>
"""


# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------

def create_app(project_dir: Path) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Console")
    tasks = TaskManager()

    def require_project() -> None:
        if not (project_dir / "project.json").exists():
            raise HTTPException(
                status_code=404,
                detail=f"Project not found: {project_dir}",
            )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        require_project()
        return PAGE

    # ------------------------------------------------------------------
    # Project / entries APIs
    # ------------------------------------------------------------------

    @app.get("/api/meta")
    def api_meta():
        require_project()
        return operations.get_project_info(project_dir)

    @app.get("/api/entries")
    def api_entries(
        q: str = Query(default=""),
        status: str = Query(default=""),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ):
        require_project()

        with ProjectStore(project_dir) as store:
            entries = store.all_entries()

        if status:
            entries = [
                entry
                for entry in entries
                if entry.status.value == status
            ]

        if q:
            query = q.lower()

            entries = [
                entry
                for entry in entries
                if query in entry.source_text.lower()
                or query in (entry.target_text or "").lower()
                or query in entry.file_path.lower()
                or query in entry.context.lower()
                or query in entry.note.lower()
            ]

        total = len(entries)

        items = []

        for entry in entries[offset:offset + limit]:
            items.append(
                {
                    "id": entry.id,
                    "file_path": entry.file_path,
                    "context": entry.context,
                    "status": entry.status.value,
                    "locked": entry.locked,
                    "ignored": entry.ignored,
                    "source_text": entry.source_text,
                    "target_text": entry.target_text or "",
                    "note": entry.note,
                }
            )

        return {
            "total": total,
            "items": items,
        }

    @app.post("/api/entries/update")
    def api_entries_update(update: EntryUpdate):
        require_project()

        with ProjectStore(project_dir) as store:
            entry = store.get_entry(update.entry_id)

            if entry is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Entry not found: {update.entry_id}",
                )

            status: Optional[EntryStatus] = None

            if update.status is not None:
                try:
                    status = EntryStatus(update.status)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status: {update.status}",
                    )

            locked_update = update.locked
            ignored_update = update.ignored

            if status == EntryStatus.LOCKED and locked_update is None:
                locked_update = True

            if status == EntryStatus.IGNORED and ignored_update is None:
                ignored_update = True

            human_reviewed: Optional[bool] = None
            machine_translated: Optional[bool] = None

            if status == EntryStatus.REVIEWED:
                human_reviewed = True
                machine_translated = False

            updated = store.update_entry(
                update.entry_id,
                target_text=update.target_text,
                status=status,
                locked=locked_update,
                ignored=ignored_update,
                human_reviewed=human_reviewed,
                machine_translated=machine_translated,
            )

            store.save_meta()

            return {
                "updated": updated,
            }

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    @app.get("/api/patches")
    def api_patches():
        require_project()
        return {
            "items": operations.list_patches(project_dir),
        }

    @app.get("/api/backups")
    def api_backups():
        require_project()
        return {
            "items": operations.list_backups_project(project_dir),
        }

    @app.get("/api/tasks")
    def api_tasks():
        return {
            "items": tasks.list(),
        }

    @app.get("/api/tasks/{task_id}")
    def api_task_detail(task_id: str):
        task = tasks.get(task_id)

        if task is None:
            raise HTTPException(
                status_code=404,
                detail=f"Task not found: {task_id}",
            )

        return task

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @app.post("/api/actions/scan")
    def action_scan():
        require_project()

        try:
            task_id = tasks.start(
                "scan",
                lambda log: operations.scan_project(
                    project_dir=project_dir,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        return {"task_id": task_id}

    @app.post("/api/actions/translate")
    def action_translate(body: TranslateRequest):
        require_project()

        try:
            task_id = tasks.start(
                "translate",
                lambda log: operations.translate_project(
                    project_dir=project_dir,
                    provider_id=body.provider,
                    model=body.model,
                    retranslate=body.retranslate,
                    no_cache=body.no_cache,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        return {"task_id": task_id}

    @app.post("/api/actions/qa")
    def action_qa(body: QARequest):
        require_project()

        try:
            task_id = tasks.start(
                "qa",
                lambda log: operations.run_qa_project(
                    project_dir=project_dir,
                    apply=body.apply,
                    apply_warnings=body.apply_warnings,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        return {"task_id": task_id}

    @app.post("/api/actions/patch")
    def action_patch(body: PatchRequest):
        require_project()

        try:
            task_id = tasks.start(
                "patch",
                lambda log: operations.patch_project(
                    project_dir=project_dir,
                    patch_name=body.patch_name,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        return {"task_id": task_id}

    @app.post("/api/actions/install")
    def action_install(body: InstallRequest):
        require_project()

        try:
            task_id = tasks.start(
                "install",
                lambda log: operations.install_project(
                    project_dir=project_dir,
                    patch_path=body.patch_path,
                    force=body.force,
                    backup=body.backup,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        return {"task_id": task_id}

    @app.post("/api/actions/rollback")
    def action_rollback(body: RollbackRequest):
        require_project()

        try:
            task_id = tasks.start(
                "rollback",
                lambda log: operations.rollback_project(
                    project_dir=project_dir,
                    backup_id=body.backup_id,
                    force=body.force,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        return {"task_id": task_id}


    # ------------------------------------------------------------------
    # Phase 10: CSV export / import / provider config / patch preview
    # ------------------------------------------------------------------

    @app.get("/api/export/csv")
    def api_export_csv(
        status: Optional[str] = Query(default=None),
    ):
        require_project()

        try:
            result = operations.export_project_csv(
                project_dir=project_dir,
                status=status,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        return FileResponse(
            path=result["path"],
            media_type="text/csv",
            filename=Path(result["path"]).name,
        )

    @app.post("/api/import/csv")
    async def api_import_csv(
        file: UploadFile = File(...),
        overwrite_locked: bool = Form(False),
    ):
        require_project()

        tmp_path: Optional[Path] = None

        try:
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".csv",
            ) as tmp:
                shutil.copyfileobj(file.file, tmp)
                tmp_path = Path(tmp.name)

            stats = operations.import_project_csv(
                project_dir=project_dir,
                csv_path=tmp_path,
                overwrite_locked=overwrite_locked,
            )

            return stats

        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    @app.get("/api/config/providers")
    def api_config_providers():
        require_project()

        return {
            "items": operations.get_provider_configs(),
        }

    @app.get("/api/patch/preview")
    def api_patch_preview():
        require_project()

        return operations.preview_patch_project(
            project_dir=project_dir,
        )
    
    return app


# ----------------------------------------------------------------------
# CLI entry
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 7.5: local web console"
    )

    parser.add_argument(
        "project_dir",
        help="Path to translation project, e.g. projects/MyGame_zh",
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

    project_dir = Path(args.project_dir)

    if not (project_dir / "project.json").exists():
        sys.exit(
            f"Project file not found: {project_dir / 'project.json'}"
        )

    app = create_app(project_dir)

    print(f"Opening web console for project: {project_dir}")
    print(f"Visit: http://{args.host}:{args.port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()