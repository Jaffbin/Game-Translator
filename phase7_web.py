from __future__ import annotations

import os
import argparse
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agl.models import EntryStatus
from agl.project import ProjectStore
from agl.qa import (
    load_glossary,
    run_qa_entries,
    summarize_issues,
)


PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer Review</title>
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

    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
      align-items: center;
    }

    input[type=text] {
      min-width: 280px;
      padding: 6px;
    }

    select, button {
      padding: 6px;
    }

    #meta {
      background: #fff;
      border: 1px solid #ddd;
      padding: 10px;
      margin-bottom: 12px;
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
  </style>
</head>
<body>
  <h1>AutoGame Localizer Review</h1>

  <div id="meta">Loading project...</div>

  <div class="toolbar">
    <input id="q" type="text" placeholder="Search source / target / file / context">
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
    <button onclick="runQA(false)">QA Check</button>
    <button onclick="runQA(true)">QA Apply</button>
    <span id="message"></span>
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

  <script>
    let offset = 0;
    const limit = 100;

    function setMessage(text, kind) {
      const el = document.getElementById("message");
      el.textContent = text || "";
      el.className = kind || "";
    }

    async function loadMeta() {
      const response = await fetch("/api/meta");
      const data = await response.json();

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

      const response = await fetch(url);
      const data = await response.json();

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

        // Status
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

        // Source
        const tdSource = document.createElement("td");
        tdSource.className = "source";
        tdSource.textContent = entry.source_text;

        // Target
        const tdTarget = document.createElement("td");
        tdTarget.className = "target";

        const textarea = document.createElement("textarea");
        textarea.className = "target";
        textarea.value = entry.target_text || "";

        tdTarget.appendChild(textarea);

        // Locked
        const tdLocked = document.createElement("td");
        tdLocked.className = "lock-cell";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "locked";
        checkbox.checked = entry.locked;

        tdLocked.appendChild(checkbox);

        // File
        const tdFile = document.createElement("td");
        tdFile.className = "file";
        tdFile.textContent = entry.file_path;

        // Save
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

      const response = await fetch("/api/entries/update", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      const result = await response.json();

      if (response.ok && result.updated) {
        setMessage("Saved " + entryId.slice(0, 8), "ok");
      } else {
        setMessage("Save failed.", "error");
      }
    }

    async function runQA(apply) {
      setMessage("Running QA...", "");

      const url = "/api/qa/run?apply=" + apply + "&apply_warnings=" + apply;

      const response = await fetch(url, {
        method: "POST"
      });

      const stats = await response.json();

      if (response.ok) {
        setMessage(
          "QA done. errors=" + (stats.entries_with_errors || 0) +
          ", warnings=" + (stats.entries_with_warnings || 0),
          "ok"
        );

        await loadMeta();
        await loadEntries();
      } else {
        setMessage("QA failed.", "error");
      }
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

    loadMeta();
    loadEntries();
  </script>
</body>
</html>
"""


class EntryUpdate(BaseModel):
    entry_id: str
    target_text: Optional[str] = None
    status: Optional[str] = None
    locked: Optional[bool] = None
    ignored: Optional[bool] = None


def create_app(project_dir: Path) -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Review")

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

    @app.get("/api/meta")
    def api_meta():
        require_project()

        with ProjectStore(project_dir) as store:
            return {
                "meta": store.meta,
                "stats": store.stats_by_status(),
            }

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

    @app.post("/api/qa/run")
    def api_qa_run(
        apply: bool = Query(default=False),
        apply_warnings: bool = Query(default=False),
    ):
        require_project()

        with ProjectStore(project_dir) as store:
            glossary_path = project_dir / "glossary.csv"
            glossary = load_glossary(glossary_path)

            entries = store.all_entries()

            results, stats = run_qa_entries(
                entries=entries,
                target_language=store.meta.get("target_language", ""),
                glossary=glossary,
            )

            if apply:
                entries_by_id = {entry.id: entry for entry in entries}
                updated = 0

                for result in results:
                    entry = entries_by_id.get(result.entry_id)

                    if entry is None:
                        continue

                    if entry.locked or entry.ignored:
                        continue

                    note = summarize_issues(result)

                    if result.errors:
                        store.update_entry(
                            entry.id,
                            status=EntryStatus.ERROR,
                            note=note,
                        )
                        updated += 1

                    elif result.warnings and apply_warnings:
                        if entry.status not in (
                            EntryStatus.REVIEWED,
                            EntryStatus.LOCKED,
                        ):
                            store.update_entry(
                                entry.id,
                                status=EntryStatus.NEEDS_REVIEW,
                                note=note,
                            )
                            updated += 1

                store.save_meta()
                stats["updated_entries"] = updated

            return stats

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 7: local web review UI"
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

    print(f"Opening review UI for project: {project_dir}")
    print(f"Visit: http://{args.host}:{args.port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()