from __future__ import annotations

import os
import shutil
import tempfile
import argparse
import sys
import threading
import traceback
import uuid
import mimetypes
import re
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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator
from agl import operations
from agl.config import load_config
from .security import add_local_security
from agl.models import EntryStatus
from agl.project import ProjectStore
from agl.qa import load_glossary, run_qa_entry
from agl.services import rpgmaker_modding
from agl.services.automatic_workflow import WorkflowError, run_automatic_workflow

# ----------------------------------------------------------------------
# Background task manager
# ----------------------------------------------------------------------
class TaskManager:
    MAX_TASKS = 200
    MAX_LOG_LINES = 2000

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
            if any(task["status"] == "running" for task in self.tasks.values()):
                raise RuntimeError(
                    "Another task is already running. "
                    "Please wait for it to finish."
                )
            completed = sorted(
                (
                    item
                    for item in self.tasks.values()
                    if item["status"] != "running"
                ),
                key=lambda item: item["created_at"],
            )
            while len(self.tasks) >= self.MAX_TASKS and completed:
                oldest = completed.pop(0)
                self.tasks.pop(oldest["id"], None)
            self.tasks[task_id] = task

        def log(message: str) -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            with self.lock:
                task["logs"].append(f"[{timestamp}] {message}")
                if len(task["logs"]) > self.MAX_LOG_LINES:
                    task["logs"] = task["logs"][-self.MAX_LOG_LINES:]

        def worker() -> None:
            try:
                result = func(log)
                with self.lock:
                    task["status"] = "completed"
                    task["message"] = "OK"
                    task["result"] = result
                log("Task completed.")
            except WorkflowError as exc:
                with self.lock:
                    task["status"] = "failed"
                    task["message"] = str(exc)
                log(f"[ERROR] {exc}")
            except Exception as exc:
                with self.lock:
                    task["status"] = "failed"
                    task["message"] = str(exc)
                log("")
                log(traceback.format_exc())

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return task_id

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            return {**task, "logs": list(task["logs"])}

    def list(self) -> list[Dict[str, Any]]:
        with self.lock:
            tasks = sorted(
                self.tasks.values(),
                key=lambda x: x["created_at"],
                reverse=True,
            )
        items = []
        for task in tasks:
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

class BulkEntryUpdate(BaseModel):
    entry_ids: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    action: str = Field(..., min_length=1, max_length=32)

class TranslateRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    retranslate: bool = False
    no_cache: bool = False

class AutoWorkflowRequest(BaseModel):
    provider: Optional[str] = None

class GlossaryUpdate(BaseModel):
    source_term: str
    target_term: str
    level: str = "required"
    case_sensitive: bool = False

class SuggestRequest(BaseModel):
    entry_id: str
    provider: Optional[str] = None
    model: Optional[str] = None
    force_ai: bool = False

class QARequest(BaseModel):
    apply: bool = False
    apply_warnings: bool = False

class PatchRequest(BaseModel):
    patch_name: Optional[str] = None

class InstallRequest(BaseModel):
    patch_path: str = Field(..., min_length=1, max_length=120)
    force: bool = False
    backup: bool = True

    @field_validator('patch_path')
    @classmethod
    def validate_patch_path(cls, v):
        # The API accepts a patch directory name, never a path. Reject rather
        # than silently removing separators, which could select another patch.
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}', v):
            raise ValueError('Invalid patch name')
        return v

class RollbackRequest(BaseModel):
    backup_id: Optional[str] = None
    force: bool = False

    @field_validator('backup_id')
    @classmethod
    def validate_backup_id(cls, v):
        if v is not None:
            # Backup ID should be a timestamp format like YYYYMMDD_HHMMSS_ffffff
            if not re.match(r'^\d{8}_\d{6}_\d+$', v):
                raise ValueError('Invalid backup ID format')
        return v


class ModChangesRequest(BaseModel):
    changes: list[Dict[str, Any]] = Field(default_factory=list, min_length=1, max_length=200)


class ModPatchRequest(ModChangesRequest):
    patch_name: Optional[str] = Field(default=None, max_length=120)

class RevisionRequest(BaseModel):
    label: str = Field(default="Checkpoint", min_length=1, max_length=120)

class SyncDecision(BaseModel):
    origin_key: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=32)

class SyncApplyRequest(BaseModel):
    decisions: list[SyncDecision] = Field(default_factory=list, min_length=1, max_length=200)

# ----------------------------------------------------------------------
# HTML page
# ----------------------------------------------------------------------
PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Project Console</title>
<style>
:root {
  --bg: #f8fafc; --surface: #ffffff; --primary: #2563eb; --primary-hover: #1d4ed8;
  --text-main: #0f172a; --text-muted: #64748b; --border: #e2e8f0;
  --success: #10b981; --warning: #f59e0b; --danger: #ef4444; --info: #3b82f6;
  --radius: 12px; --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text-main); display: flex; min-height: 100vh; }

/* Sidebar */
.sidebar { width: 220px; background: #0f172a; color: #cbd5e1; padding: 20px 12px; flex-shrink: 0; position: sticky; top: 0; height: 100vh; overflow-y: auto; display: flex; flex-direction: column; }
.sidebar .logo { color: #fff; font-weight: 700; font-size: 15px; padding: 0 10px 18px 10px; border-bottom: 1px solid #1e293b; margin-bottom: 12px; }
.sidebar a { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-radius: 8px; color: #cbd5e1; text-decoration: none; font-size: 14px; margin-bottom: 4px; }
.sidebar a.active, .sidebar a:hover { background: #1e293b; color: #fff; }
.sidebar a.home { margin-top: auto; color: #94a3b8; border-top: 1px solid #1e293b; padding-top: 16px; border-radius: 0; }

/* Main Content */
.main { flex: 1; padding: 24px 32px; overflow-x: hidden; min-width: 0; }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; gap: 16px; }
.head h1 { margin: 0; font-size: 24px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.badge { display: inline-block; padding: 4px 10px; border-radius: 99px; font-size: 12px; font-weight: 600; }
.badge.green { background: #dcfce7; color: #166534; }
.badge.gray { background: #e2e8f0; color: #475569; }
.badge.amber { background: #fef3c7; color: #92400e; }
.badge.blue { background: #dbeafe; color: #1e40af; }
.badge.red { background: #fee2e2; color: #991b1b; }
.lang-flow { color: var(--text-muted); font-size: 14px; font-weight: normal; }

/* Action Bar */
.action-bar { display: flex; gap: 8px; flex-wrap: wrap; }
button { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border); background: #fff; font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.15s; display: inline-flex; align-items: center; gap: 6px; }
button:hover { background: #f1f5f9; border-color: #cbd5e1; }
button.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
button.primary:hover { background: var(--primary-hover); }
button.danger { color: var(--danger); border-color: #fecaca; }
button.danger:hover { background: #fef2f2; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.small { padding: 4px 10px; font-size: 12px; }

/* Stats Grid */
.stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; }
.stat-label { font-size: 13px; color: var(--text-muted); margin-bottom: 4px; }
.stat-value { font-size: 24px; font-weight: 700; }
.stat-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

/* Cards & Panels */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px 24px; margin-bottom: 24px; box-shadow: var(--shadow); }
.card h3 { margin: 0 0 16px 0; font-size: 16px; display: flex; align-items: center; justify-content: space-between; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; padding: 10px 12px; border-bottom: 1px solid var(--border); background: #f8fafc; position: sticky; top: 0; z-index: 1; }
td { padding: 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:hover td { background: #f8fafc; }
.muted { color: var(--text-muted); font-size: 13px; }

/* Forms & Inputs */
input, select, textarea { padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; outline: none; font-family: inherit; background: #fff; }
input:focus, select:focus, textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
textarea { resize: vertical; min-height: 60px; width: 100%; }
.toolbar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.toolbar input[type="text"] { flex: 1; min-width: 200px; }

/* Terminal */
.terminal { background: #0f172a; color: #a7f3d0; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; padding: 16px; border-radius: 8px; height: 280px; overflow-y: auto; white-space: pre-wrap; line-height: 1.5; }

/* Layout helpers */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 1024px) { .grid-2 { grid-template-columns: 1fr; } .stats-grid { grid-template-columns: repeat(2, 1fr); } }

/* Pulse animation */
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.running { animation: pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite; color: var(--info); font-weight: 600; }

/* Modal */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100; align-items: center; justify-content: center; }
.modal-overlay.active { display: flex; }
.modal { background: #fff; padding: 24px; border-radius: 12px; width: 480px; max-width: 90vw; box-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1); }
.modal h3 { margin: 0 0 16px 0; font-size: 18px; }
.modal .form-group { margin-bottom: 16px; }
.modal label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; color: var(--text-muted); }
.modal input, .modal select { width: 100%; }
.modal .actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 24px; }
</style>
</head>
<body>
  <div class="sidebar">
    <div class="logo">⌘ Developer Workspace</div>
    <a href="__PORTAL_URL__">◫ 项目列表</a>
    <a href="#" class="active">▤ Project Console</a>
    <a href="__PORTAL_URL__">⚙ Settings</a>
    <a href="__PORTAL_URL__" class="home">← 返回首页</a>
  </div>

  <div class="main">
    <!-- Header -->
    <div class="head">
      <h1>
        <span id="project_name">Loading...</span>
        <span class="badge green" id="project_status">Ready</span>
        <span class="lang-flow" id="lang_flow"></span>
      </h1>
      <div class="action-bar">
        <button onclick="startTask('/api/actions/scan', {})">Scan</button>
        <button onclick="showTranslateModal()">Translate</button>
        <button onclick="startTask('/api/actions/qa', {apply:false})">QA Check</button>
        <button onclick="startTask('/api/actions/qa', {apply:true, apply_warnings:true})">QA Apply</button>
        <button class="primary" onclick="startTask('/api/actions/patch', {})">Generate Patch</button>
        <button onclick="showInstallModal()">Install</button>
        <button class="danger" onclick="showRollbackModal()">Rollback</button>
      </div>
    </div>

    <!-- Stats -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Entries</div>
        <div class="stat-value" id="stat_total">0</div>
        <div class="stat-sub" id="stat_translated">0 translated</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">QA Issues</div>
        <div class="stat-value" id="stat_qa">0</div>
        <div class="stat-sub" id="stat_qa_err">0 critical</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Patch</div>
        <div class="stat-value" id="stat_patch">0%</div>
        <div class="stat-sub">ready to preview</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Last Run</div>
        <div class="stat-value" style="font-size:18px;" id="stat_last_run">Never</div>
        <div class="stat-sub" id="stat_last_task">-</div>
      </div>
    </div>

    <!-- Tasks & Logs -->
    <div class="grid-2">
      <div class="card">
        <h3>Tasks <button class="small" onclick="refreshTasks()">Refresh</button></h3>
        <table>
          <thead><tr><th>Task</th><th>Message</th><th>Status</th></tr></thead>
          <tbody id="tasks_body">
            <tr><td colspan="3" class="muted" style="text-align:center;">No tasks yet</td></tr>
          </tbody>
        </table>
      </div>
      <div class="card">
        <h3>Logs</h3>
        <div class="terminal" id="log_terminal">[System] Ready. Select an action to begin.</div>
      </div>
    </div>

    <!-- Entries Review -->
    <div class="card">
      <h3>
        Entries 
        <span class="muted" style="font-weight:normal;font-size:14px;" id="entries_count"></span>
      </h3>
      <div class="toolbar">
        <input type="text" id="search_q" placeholder="Search source, target, file...">
        <select id="filter_status">
          <option value="">All Statuses</option>
          <option value="pending">Pending</option>
          <option value="machine_translated">Machine Translated</option>
          <option value="needs_review">Needs Review</option>
          <option value="reviewed">Reviewed</option>
          <option value="locked">Locked</option>
          <option value="error">Error</option>
        </select>
        <button class="primary" onclick="loadEntries()">Search</button>
        <button onclick="exportCsv()">Export CSV</button>
      </div>
      <div style="max-height: 600px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px;">
        <table>
          <thead>
            <tr>
              <th style="width:25%">Source</th>
              <th style="width:35%">Translation</th>
              <th style="width:10%">Status</th>
              <th style="width:5%">Lock</th>
              <th style="width:15%">File</th>
              <th style="width:10%">Actions</th>
            </tr>
          </thead>
          <tbody id="entries_body"></tbody>
        </table>
      </div>
      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:16px;">
        <button id="btn_prev" onclick="prevPage()" disabled>← Previous</button>
        <span class="muted" id="page_info"></span>
        <button id="btn_next" onclick="nextPage()">Next →</button>
      </div>
    </div>
  </div>

  <!-- Modals -->
  <div class="modal-overlay" id="modal_overlay">
    <div class="modal" id="modal_content"></div>
  </div>

<script>
  const PORTAL_URL = "__PORTAL_URL__";
  let currentOffset = 0;
  const pageSize = 50;
  let totalEntries = 0;
  let pollingTimer = null;

  async function api(url, method, body, isForm) {
    const opts = { method: method || "GET", headers: {} };
    if (body && !isForm) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    if (body && isForm) { opts.body = body; }
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  async function init() {
    await loadMeta();
    await loadEntries();
    refreshTasks();
  }

  async function loadMeta() {
    try {
      const data = await api("/api/meta");
      const meta = data.meta || {};
      const stats = data.stats || {};
      
      document.getElementById("project_name").textContent = meta.name || "Unknown Project";
      document.getElementById("lang_flow").textContent = `Source → ${meta.target_language || "zh-CN"}`;
      
      const total = Object.values(stats).reduce((a, b) => a + b, 0);
      const translated = (stats.machine_translated || 0) + (stats.reviewed || 0) + (stats.needs_review || 0);
      const errors = stats.error || 0;
      
      document.getElementById("stat_total").textContent = total.toLocaleString();
      document.getElementById("stat_translated").textContent = `${translated} processed`;
      document.getElementById("stat_qa").textContent = errors;
      document.getElementById("stat_qa_err").textContent = `${errors} critical`;
      
      const patchReady = total > 0 ? Math.round((translated / total) * 100) : 0;
      document.getElementById("stat_patch").textContent = patchReady + "%";
    } catch (err) {
      console.error("Failed to load meta", err);
    }
  }

  async function loadEntries() {
    const q = document.getElementById("search_q").value;
    const status = document.getElementById("filter_status").value;
    try {
      const data = await api(`/api/entries?q=${encodeURIComponent(q)}&status=${status}&limit=${pageSize}&offset=${currentOffset}`);
      totalEntries = data.total || 0;
      renderEntries(data.items || []);
      document.getElementById("entries_count").textContent = `${totalEntries.toLocaleString()} total`;
      document.getElementById("page_info").textContent = `Showing ${totalEntries === 0 ? 0 : currentOffset + 1}-${Math.min(currentOffset + pageSize, totalEntries)} of ${totalEntries}`;
      document.getElementById("btn_prev").disabled = currentOffset === 0;
      document.getElementById("btn_next").disabled = currentOffset + pageSize >= totalEntries;
    } catch (err) {
      console.error("Failed to load entries", err);
    }
  }

  function renderEntries(items) {
    const tbody = document.getElementById("entries_body");
    tbody.innerHTML = "";
    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted" style="text-align:center;">No entries found.</td></tr>`;
      return;
    }
    items.forEach(e => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td style="white-space:pre-wrap;">${escapeHtml(e.source_text)}</td>
        <td><textarea class="target-text" data-id="${e.id}">${escapeHtml(e.target_text || "")}</textarea></td>
        <td><span class="badge ${getStatusColor(e.status)}">${e.status}</span></td>
        <td style="text-align:center;"><input type="checkbox" class="lock-cb" data-id="${e.id}" ${e.locked ? "checked" : ""}></td>
        <td class="muted" style="font-size:12px;">${escapeHtml(e.file_path)}</td>
        <td><button class="small primary" onclick="saveEntry('${e.id}')">Save</button></td>
      `;
      tbody.appendChild(tr);
    });
  }

  function getStatusColor(s) {
    if (s === "reviewed") return "green";
    if (s === "machine_translated") return "blue";
    if (s === "needs_review") return "amber";
    if (s === "error") return "red";
    if (s === "locked") return "gray";
    return "gray";
  }

  async function saveEntry(id) {
    const row = document.querySelector(`textarea[data-id="${id}"]`).closest("tr");
    const target = row.querySelector(".target-text").value;
    const locked = row.querySelector(".lock-cb").checked;
    const status = locked ? "locked" : "reviewed"; // Auto mark as reviewed if manually saved
    
    try {
      await api("/api/entries/update", "POST", { 
        entry_id: id, 
        target_text: target, 
        locked: locked,
        status: status
      });
      loadMeta();
      loadEntries();
    } catch (err) { alert("Save failed: " + err); }
  }

  function prevPage() { currentOffset = Math.max(0, currentOffset - pageSize); loadEntries(); }
  function nextPage() { currentOffset += pageSize; loadEntries(); }
  
  function exportCsv() { 
    const status = document.getElementById("filter_status").value;
    window.location.href = "/api/export/csv?status=" + encodeURIComponent(status); 
  }

  async function startTask(url, body) {
    try {
      const data = await api(url, "POST", body);
      appendLog(`[System] Task started: ${data.task_id}`);
      startPolling();
    } catch (err) { alert("Failed to start task: " + err); }
  }

  function startPolling() {
    if (pollingTimer) return;
    pollingTimer = setInterval(refreshTasks, 2000);
  }

  async function refreshTasks() {
    try {
      const data = await api("/api/tasks");
      const items = data.items || [];
      const tbody = document.getElementById("tasks_body");
      tbody.innerHTML = "";
      
      let hasRunning = false;
      if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="3" class="muted" style="text-align:center;">No tasks yet</td></tr>`;
      } else {
        items.slice(0, 10).forEach(t => {
          if (t.status === "running") hasRunning = true;
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td><b>${t.name}</b><br><span class="muted" style="font-size:11px;">${t.created_at || ""}</span></td>
            <td class="muted">${t.message || "-"}</td>
            <td class="${t.status === 'running' ? 'running' : ''}">${t.status}</td>
          `;
          tr.style.cursor = "pointer";
          tr.onclick = () => viewTaskLogs(t.id);
          tbody.appendChild(tr);
        });
      }

      if (!hasRunning && pollingTimer) {
        clearInterval(pollingTimer);
        pollingTimer = null;
        loadMeta(); 
        loadEntries();
      }
    } catch (err) { console.error(err); }
  }

  async function viewTaskLogs(taskId) {
    try {
      const task = await api(`/api/tasks/${taskId}`);
      document.getElementById("log_terminal").textContent = (task.logs || []).join("\\n");
      document.getElementById("log_terminal").scrollTop = document.getElementById("log_terminal").scrollHeight;
    } catch (err) { console.error(err); }
  }
  
  function appendLog(msg) {
    const terminal = document.getElementById("log_terminal");
    terminal.textContent += "\\n" + msg;
    terminal.scrollTop = terminal.scrollHeight;
  }

  function showModal(html) {
    document.getElementById("modal_content").innerHTML = html;
    document.getElementById("modal_overlay").classList.add("active");
  }
  function hideModal() {
    document.getElementById("modal_overlay").classList.remove("active");
  }

  async function showTranslateModal() {
    let providersHtml = '<option value="">Default (from config)</option>';
    try {
      const data = await api("/api/config/providers");
      (data.items || []).forEach(p => {
        const label = p.id + (p.has_api_key ? "" : " (no key)");
        providersHtml += `<option value="${p.id}">${label}</option>`;
      });
    } catch(e) {}
    
    showModal(`
      <h3>Translate Options</h3>
      <div class="form-group">
        <label>Provider</label>
        <select id="m_provider">${providersHtml}</select>
      </div>
      <div class="form-group">
        <label>Model (optional override)</label>
        <input type="text" id="m_model" placeholder="Leave empty to use default">
      </div>
      <div class="form-group">
        <label><input type="checkbox" id="m_retranslate"> Force retranslate all (ignore cache & reviewed)</label>
      </div>
      <div class="actions">
        <button onclick="hideModal()">Cancel</button>
        <button class="primary" onclick="doTranslate()">Start Translate</button>
      </div>
    `);
  }

  async function doTranslate() {
    const body = {
      provider: document.getElementById("m_provider").value || null,
      model: document.getElementById("m_model").value || null,
      retranslate: document.getElementById("m_retranslate").checked,
      no_cache: false
    };
    hideModal();
    await startTask("/api/actions/translate", body);
  }

  async function showInstallModal() {
    let patchesHtml = '<option value="">-- select patch --</option>';
    try {
      const data = await api("/api/patches");
      (data.items || []).forEach(p => {
        patchesHtml += `<option value="${p.path}">${p.name}</option>`;
      });
    } catch(e) {}

    showModal(`
      <h3>Install Patch</h3>
      <p class="muted">This will backup your game files and apply the selected patch.</p>
      <div class="form-group">
        <label>Select Patch</label>
        <select id="m_patch">${patchesHtml}</select>
      </div>
      <div class="form-group">
        <label><input type="checkbox" id="m_force"> Force install (ignore hash mismatch)</label>
      </div>
      <div class="actions">
        <button onclick="hideModal()">Cancel</button>
        <button class="primary" onclick="doInstall()">Confirm Install</button>
      </div>
    `);
  }

  async function doInstall() {
    const patchPath = document.getElementById("m_patch").value;
    if (!patchPath) { alert("Please select a patch."); return; }
    const body = {
      patch_path: patchPath,
      force: document.getElementById("m_force").checked,
      backup: true
    };
    hideModal();
    await startTask("/api/actions/install", body);
  }

  async function showRollbackModal() {
    let backupsHtml = '<option value="">-- latest backup --</option>';
    try {
      const data = await api("/api/backups");
      (data.items || []).forEach(b => {
        backupsHtml += `<option value="${b.backup_id}">${b.backup_id} (${(b.files || []).length} files)</option>`;
      });
    } catch(e) {}

    showModal(`
      <h3>Rollback</h3>
      <p class="muted">Restore game files to a previous backup state.</p>
      <div class="form-group">
        <label>Select Backup</label>
        <select id="m_backup">${backupsHtml}</select>
      </div>
      <div class="actions">
        <button onclick="hideModal()">Cancel</button>
        <button class="danger" onclick="doRollback()">Confirm Rollback</button>
      </div>
    `);
  }

  async function doRollback() {
    const body = {
      backup_id: document.getElementById("m_backup").value || null,
      force: false
    };
    hideModal();
    await startTask("/api/actions/rollback", body);
  }

  function escapeHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  document.getElementById("search_q").addEventListener("keydown", e => {
    if (e.key === "Enter") { currentOffset = 0; loadEntries(); }
  });

  init();
</script>
</body>
</html>
"""

# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------
def create_app(
    project_dir: Path,
    *,
    page: Optional[str] = None,
    portal_url: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Console")
    if page is None:
        from ui.pages import CONSOLE_PAGE

        console_page = CONSOLE_PAGE
    else:
        console_page = page

    # Add CORS middleware with restricted origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://localhost:8000", "http://127.0.0.1", "http://127.0.0.1:8000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    add_local_security(app)

    tasks = TaskManager()

    def require_project() -> None:
        if not (project_dir / "project.json").exists():
            raise HTTPException(
                status_code=404,
                detail=f"Project not found: {project_dir}",
            )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        resolved_portal_url = portal_url or os.environ.get(
            "AGL_PORTAL_URL", "http://127.0.0.1:8300/"
        )
        return console_page.replace("__PORTAL_URL__", resolved_portal_url)

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

            if updated and (update.target_text is not None or status == EntryStatus.REVIEWED):
                reviewed_entry = store.get_entry(update.entry_id)
                if reviewed_entry is not None and reviewed_entry.human_reviewed:
                    operations.remember_reviewed_translations(
                        [reviewed_entry],
                        store.meta.get("target_language") or "",
                    )

            return {
                "updated": updated,
            }

    @app.post("/api/entries/bulk-update")
    def api_entries_bulk_update(body: BulkEntryUpdate):
        require_project()
        action = body.action.strip().lower()
        if action not in {"reviewed", "lock", "unlock"}:
            raise HTTPException(status_code=400, detail="Unsupported bulk entry action.")
        ids = list(dict.fromkeys(str(x).strip() for x in body.entry_ids if str(x).strip()))
        if not ids:
            raise HTTPException(status_code=400, detail="No entry ids supplied.")
        updated = skipped = missing = 0
        reviewed_entries = []
        target_language = ""
        with ProjectStore(project_dir) as store:
            target_language = store.meta.get("target_language") or ""
            for entry_id in ids:
                entry = store.get_entry(entry_id)
                if entry is None:
                    missing += 1
                    continue
                if entry.ignored:
                    skipped += 1
                    continue
                if action == "reviewed":
                    if entry.locked or not (entry.target_text or "").strip():
                        skipped += 1
                        continue
                    changed = store.update_entry(entry.id, status=EntryStatus.REVIEWED, machine_translated=False, human_reviewed=True)
                elif action == "lock":
                    changed = store.update_entry(entry.id, status=EntryStatus.LOCKED, locked=True)
                else:
                    next_status = EntryStatus.MACHINE_TRANSLATED if (entry.target_text or "").strip() else EntryStatus.PENDING
                    changed = store.update_entry(entry.id, status=next_status, locked=False)
                if changed:
                    updated += 1
                    if action == "reviewed":
                        reviewed_entry = store.get_entry(entry.id)
                        if reviewed_entry is not None:
                            reviewed_entries.append(reviewed_entry)
                else:
                    skipped += 1
            store.save_meta()
        if reviewed_entries:
            operations.remember_reviewed_translations(
                reviewed_entries,
                target_language,
            )
        return {"updated": updated, "skipped": skipped, "missing": missing}

    @app.get("/api/qa/issues")
    def api_qa_issues(limit: int = Query(default=100, ge=1, le=500)):
        require_project()
        with ProjectStore(project_dir) as store:
            entries = store.all_entries()
        all_items = []
        for entry in entries:
            if entry.status not in (EntryStatus.ERROR, EntryStatus.NEEDS_REVIEW):
                continue
            all_items.append({
                "entry_id": entry.id,
                "status": entry.status.value,
                "file_path": entry.file_path,
                "source_text": entry.source_text,
                "target_text": entry.target_text or "",
                "note": entry.note or "",
            })
        return {"total": len(all_items), "items": all_items[:limit]}

    # ------------------------------------------------------------------
    # Glossary / translation memory / single-entry AI assistant
    # ------------------------------------------------------------------
    @app.get("/api/glossary")
    def api_glossary(q: str = Query(default="")):
        require_project()
        return {"items": operations.list_project_glossary(project_dir, q)}

    @app.post("/api/glossary")
    def api_glossary_update(body: GlossaryUpdate):
        require_project()
        try:
            return operations.upsert_project_glossary(
                project_dir,
                source_term=body.source_term,
                target_term=body.target_term,
                level=body.level,
                case_sensitive=body.case_sensitive,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/glossary")
    def api_glossary_delete(source_term: str = Query(...)):
        require_project()
        try:
            removed = operations.delete_project_glossary(project_dir, source_term)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not removed:
            raise HTTPException(status_code=404, detail="Glossary term not found.")
        return {"deleted": True}

    @app.get("/api/translation-memory")
    def api_translation_memory(
        q: str = Query(default=""),
        language: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        require_project()
        return {"items": operations.search_translation_memory(q, language, limit)}

    @app.get("/api/entries/{entry_id}/assistant-context")
    def api_entry_assistant_context(entry_id: str):
        """Return the non-secret context needed by the Translation Editor assistant panel."""
        require_project()
        with ProjectStore(project_dir) as store:
            entry = store.get_entry(entry_id)
            if entry is None:
                raise HTTPException(status_code=404, detail="Entry not found.")
            entries = store.all_entries()
            target_language = store.meta.get("target_language") or ""

        glossary = load_glossary(Path(project_dir) / "glossary.csv")
        source = entry.source_text or ""
        matched_glossary = []
        for item in glossary:
            haystack = source if item.case_sensitive else source.lower()
            needle = item.source_term if item.case_sensitive else item.source_term.lower()
            if needle and needle in haystack:
                matched_glossary.append({
                    "source_term": item.source_term,
                    "target_term": item.target_term,
                    "level": item.level,
                    "case_sensitive": item.case_sensitive,
                })

        qa_result = run_qa_entry(entry, target_language=target_language, glossary=glossary)
        qa_issues = [
            {"level": issue.level, "code": issue.code, "message": issue.message}
            for issue in qa_result.issues
        ]

        same_file = [item for item in entries if item.file_path == entry.file_path]
        position = next((i for i, item in enumerate(same_file) if item.id == entry.id), -1)
        neighbors = []
        if position > 0:
            prev = same_file[position - 1]
            neighbors.append({"relation": "previous", "id": prev.id, "source_text": prev.source_text, "target_text": prev.target_text or "", "status": prev.status.value})
        if position >= 0 and position + 1 < len(same_file):
            nxt = same_file[position + 1]
            neighbors.append({"relation": "next", "id": nxt.id, "source_text": nxt.source_text, "target_text": nxt.target_text or "", "status": nxt.status.value})

        memory = operations.search_translation_memory(source, target_language, limit=8) if source else []
        exact = [item for item in memory if (item.get("source_text") or "").strip() == source.strip()]
        return {
            "entry": {
                "id": entry.id,
                "source_text": source,
                "target_text": entry.target_text or "",
                "context": entry.context or "",
                "file_path": entry.file_path,
                "engine": entry.engine,
                "location": entry.location,
                "note": entry.note or "",
                "status": entry.status.value,
                "locked": entry.locked,
                "ignored": entry.ignored,
            },
            "glossary": matched_glossary,
            "translation_memory": {"exact": exact[:1], "matches": memory},
            "qa": {"passed": qa_result.passed, "issues": qa_issues},
            "neighbors": neighbors,
        }

    @app.post("/api/entries/suggest")
    def api_entries_suggest(body: SuggestRequest):
        require_project()
        try:
            return operations.suggest_entry_translation(
                project_dir,
                entry_id=body.entry_id,
                provider_id=body.provider,
                model=body.model,
                force_ai=body.force_ai,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail="无法生成建议，请检查 Provider 配置。") from exc

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

    @app.get("/api/history")
    def api_history(limit: int = Query(default=100, ge=1, le=500)):
        require_project()
        with ProjectStore(project_dir) as store:
            return {"items": store.history(limit=limit)}

    @app.get("/api/entries/{entry_id}/history")
    def api_entry_history(entry_id: str, limit: int = Query(default=50, ge=1, le=200)):
        require_project()
        with ProjectStore(project_dir) as store:
            if store.get_entry(entry_id) is None:
                raise HTTPException(status_code=404, detail="Entry not found.")
            return {"items": store.history(entry_id=entry_id, limit=limit)}

    @app.get("/api/revisions")
    def api_revisions(limit: int = Query(default=50, ge=1, le=200)):
        require_project()
        with ProjectStore(project_dir) as store:
            return {"items": store.list_revisions(limit=limit)}

    @app.post("/api/revisions")
    def api_create_revision(body: RevisionRequest):
        require_project()
        with ProjectStore(project_dir) as store:
            return store.create_revision(body.label)

    @app.get("/api/revisions/{revision_id}/diff")
    def api_revision_diff(revision_id: str, limit: int = Query(default=100, ge=1, le=500)):
        require_project()
        try:
            with ProjectStore(project_dir) as store:
                return store.revision_diff(revision_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

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
    @app.post("/api/actions/auto")
    def action_auto(body: Optional[AutoWorkflowRequest] = None):
        require_project()
        try:
            task_id = tasks.start(
                "auto",
                lambda log: run_automatic_workflow(project_dir=project_dir, log=log, provider_id=body.provider if body else None),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

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

    @app.post("/api/actions/sync-preview")
    def action_sync_preview():
        require_project()
        try:
            task_id = tasks.start(
                "sync-preview",
                lambda log: operations.preview_update_sync(project_dir=project_dir, log=log),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"task_id": task_id}

    @app.post("/api/actions/sync-apply")
    def action_sync_apply(body: SyncApplyRequest):
        require_project()
        payload = [{"origin_key": item.origin_key, "action": item.action} for item in body.decisions]
        try:
            task_id = tasks.start(
                "sync-apply",
                lambda log: operations.apply_update_sync(project_dir=project_dir, decisions=payload, log=log),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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
        patches_dir = project_dir / "patches"
        safe_patch_name = body.patch_path
        full_patch_path = (patches_dir / safe_patch_name).resolve()

        # Verify the resolved path is within the patches directory
        try:
            full_patch_path.relative_to(patches_dir.resolve())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid patch path: must be within project patches directory"
            )

        if not full_patch_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Patch not found: {safe_patch_name}"
            )

        try:
            task_id = tasks.start(
                "install",
                lambda log: operations.install_project(
                    project_dir=project_dir,
                    patch_path=full_patch_path,
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
        # Validate backup_id if provided
        if body.backup_id is not None:
            safe_backup_id = body.backup_id

            # Verify backup exists and is within backups directory
            backups_dir = project_dir / "backups"
            backup_path = (backups_dir / safe_backup_id).resolve()

            try:
                backup_path.relative_to(backups_dir.resolve())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid backup ID: must be within project backups directory"
                )

            if not backup_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Backup not found: {safe_backup_id}"
                )
        else:
            safe_backup_id = None

        try:
            task_id = tasks.start(
                "rollback",
                lambda log: operations.rollback_project(
                    project_dir=project_dir,
                    backup_id=safe_backup_id,
                    force=body.force,
                    log=log,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"task_id": task_id}

    # ------------------------------------------------------------------
    # CSV export / import / provider config / patch preview
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
        # Validate file type - only allow CSV
        if file.filename is None:
            raise HTTPException(status_code=400, detail="No filename provided")

        # Check file extension
        if not file.filename.lower().endswith('.csv'):
            raise HTTPException(status_code=400, detail="Only CSV files are allowed")

        # Validate MIME type
        allowed_mime_types = ['text/csv', 'application/vnd.ms-excel']
        if file.content_type and file.content_type not in allowed_mime_types:
            raise HTTPException(status_code=400, detail="Invalid file type. Only CSV files are allowed")

        # Limit file size (10MB max)
        MAX_FILE_SIZE = 10 * 1024 * 1024
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large. Maximum size is 10MB")

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".csv",
            ) as tmp:
                tmp.write(content)
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
            "default": load_config().first_available_provider(),
        }

    @app.get("/api/patch/preview")
    def api_patch_preview():
        require_project()
        return operations.preview_patch_project(
            project_dir=project_dir,
        )

    @app.get("/api/modding/categories")
    def api_modding_categories():
        require_project()
        with ProjectStore(project_dir) as store:
            if store.meta.get("engine") != "rpgmaker_mv_mz":
                raise HTTPException(status_code=400, detail="Data modifier supports RPG Maker MV/MZ projects only.")
        return {"items": rpgmaker_modding.categories()}

    @app.get("/api/modding/catalog")
    def api_modding_catalog(
        category: str = Query(..., min_length=1, max_length=32),
        q: str = Query(default="", max_length=120),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        require_project()
        try:
            with ProjectStore(project_dir) as store:
                game_path = store.meta["game_path"]
                if store.meta.get("engine") != "rpgmaker_mv_mz":
                    raise ValueError("Data modifier supports RPG Maker MV/MZ projects only.")
            return rpgmaker_modding.catalog(game_path, category, q, limit, offset)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/modding/preview")
    def api_modding_preview(body: ModChangesRequest):
        require_project()
        try:
            with ProjectStore(project_dir) as store:
                game_path = store.meta["game_path"]
            return rpgmaker_modding.preview(game_path, body.changes)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/modding/patch")
    def api_modding_patch(body: ModPatchRequest):
        require_project()
        try:
            return rpgmaker_modding.build_patch(
                project_dir,
                body.changes,
                patch_name=body.patch_name,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app

# ----------------------------------------------------------------------
# CLI entry
# ----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="AutoGame Localizer project console"
    )
    parser.add_argument(
        "project_dir",
        help="Path to translation project, e.g. projects/MyGame_zh",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the server to (default: 127.0.0.1). Use 0.0.0.0 to expose to all interfaces (not recommended for security)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow remote connections by binding to 0.0.0.0. WARNING: This exposes the server to all network interfaces and is not recommended without additional security measures.",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    if not (project_dir / "project.json").exists():
        sys.exit(
            f"Project file not found: {project_dir / 'project.json'}"
        )

    # Security warning for remote access
    host = args.host
    if args.allow_remote:
        host = "0.0.0.0"
        print("=" * 60)
        print("WARNING: Server will be accessible from all network interfaces!")
        print("This is a LOCAL development tool and should NOT be exposed")
        print("to untrusted networks without proper authentication.")
        print("Consider using a reverse proxy with authentication.")
        print("=" * 60)

    app = create_app(project_dir)
    print(f"Opening web console for project: {project_dir}")
    print(f"Visit: http://{host}:{args.port}")

    if host == "127.0.0.1":
        print("Server is only accessible from localhost (secure).")
    else:
        print(f"WARNING: Server is accessible from all interfaces at {host}")

    uvicorn.run(
        app,
        host=host,
        port=args.port,
    )

if __name__ == "__main__":
    main()
