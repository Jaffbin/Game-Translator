from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import uvicorn

from agl.engines import detect_handler
from agl.project import ProjectStore
from agl.workspace import (
    ensure_workspace,
    projects_root,
    sanitize_project_name,
)

from phase75_web import create_app


# ----------------------------------------------------------------------
# Network helpers
# ----------------------------------------------------------------------

def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start_port: int = 8000, max_attempts: int = 100) -> int:
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
# Web console manager
# ----------------------------------------------------------------------

class WebConsoleManager:
    """
    Runs one local web console at a time.

    If another project is opened, the previous console is stopped.
    """

    def __init__(self):
        self.server: Optional[uvicorn.Server] = None
        self.thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None
        self.project_dir: Optional[Path] = None

    def is_running(self) -> bool:
        return (
            self.server is not None
            and self.thread is not None
            and self.thread.is_alive()
            and not getattr(self.server, "should_exit", False)
        )

    def start(self, project_dir: Path | str) -> int:
        project_dir = Path(project_dir).resolve()

        if self.is_running():
            if self.project_dir == project_dir:
                webbrowser.open(f"http://127.0.0.1:{self.port}")
                return self.port

            self.stop()

        port = find_free_port()

        app = create_app(project_dir)

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )

        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = port
        self.project_dir = project_dir

        self.thread.start()

        if not wait_for_port(port):
            self.stop()
            raise RuntimeError(
                "Web console did not start in time. "
                "Check console output for errors."
            )

        webbrowser.open(f"http://127.0.0.1:{port}")

        return port

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True

        if self.thread is not None:
            self.thread.join(timeout=3)

        self.server = None
        self.thread = None
        self.port = None
        self.project_dir = None


# ----------------------------------------------------------------------
# Launcher UI
# ----------------------------------------------------------------------

class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AutoGame Localizer Launcher")
        self.root.geometry("980x560")

        self.project_paths: Dict[str, Path] = {}
        self.console_manager = WebConsoleManager()

        ensure_workspace()
        self.setup_ui()
        self.refresh_projects()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def setup_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=10)
        toolbar.pack(fill=tk.X)

        ttk.Button(
            toolbar,
            text="Refresh",
            command=self.refresh_projects,
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            toolbar,
            text="Create Project",
            command=self.create_project,
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            toolbar,
            text="Open Web Console",
            command=self.open_console,
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            toolbar,
            text="Open Project Folder",
            command=self.open_project_folder,
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(
            toolbar,
            text=f"Projects root: {projects_root()}",
        ).pack(side=tk.RIGHT)

        columns = ("name", "engine", "target_language", "game_path")

        self.tree = ttk.Treeview(
            self.root,
            columns=columns,
            show="headings",
        )

        self.tree.heading("name", text="Project")
        self.tree.heading("engine", text="Engine")
        self.tree.heading("target_language", text="Target Language")
        self.tree.heading("game_path", text="Game Path")

        self.tree.column("name", width=180)
        self.tree.column("engine", width=160)
        self.tree.column("target_language", width=120)
        self.tree.column("game_path", width=480)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.tree.bind("<Double-1>", lambda event: self.open_console())

    # ------------------------------------------------------------------
    # Project list
    # ------------------------------------------------------------------

    def refresh_projects(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.project_paths.clear()

        root_dir = projects_root()

        if not root_dir.exists():
            return

        for project_dir in sorted(root_dir.iterdir()):
            if not project_dir.is_dir():
                continue

            project_file = project_dir / "project.json"

            if not project_file.exists():
                continue

            try:
                meta = json.loads(project_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            name = project_dir.name

            self.project_paths[name] = project_dir

            self.tree.insert(
                "",
                tk.END,
                iid=name,
                values=(
                    name,
                    meta.get("engine", ""),
                    meta.get("target_language", ""),
                    meta.get("game_path", ""),
                ),
            )

    def selected_project_dir(self) -> Optional[Path]:
        selected = self.tree.focus()

        if not selected:
            messagebox.showwarning(
                "No project selected",
                "Please select a project first.",
            )
            return None

        return self.project_paths.get(selected)

    # ------------------------------------------------------------------
    # Create project
    # ------------------------------------------------------------------

    def create_project(self) -> None:
        name = simpledialog.askstring(
            "Create Project",
            "Project name:",
            parent=self.root,
        )

        if not name:
            return

        safe_name = sanitize_project_name(name)
        project_dir = projects_root() / safe_name

        if project_dir.exists():
            messagebox.showerror(
                "Project already exists",
                f"Project folder already exists:\n\n{project_dir}",
            )
            return

        game_path = filedialog.askdirectory(
            title="Select game root folder",
        )

        if not game_path:
            return

        target_language = simpledialog.askstring(
            "Target Language",
            "Target language:",
            initialvalue="zh-CN",
            parent=self.root,
        )

        if not target_language:
            target_language = "zh-CN"

        handler = detect_handler(game_path)

        if handler is None:
            messagebox.showerror(
                "Unsupported game",
                "Could not detect a supported game engine.\n\n"
                "Supported engines:\n"
                "  - RPG Maker MV/MZ\n"
                "  - Ren'Py translation templates\n"
                "  - Unity lightweight text files",
            )
            return

        try:
            store = ProjectStore.create(
                project_dir=project_dir,
                game_path=game_path,
                engine_id=handler.engine_id,
                target_language=target_language.strip(),
                name=safe_name,
            )
            store.close()
        except Exception as exc:
            messagebox.showerror(
                "Failed to create project",
                str(exc),
            )
            return

        self.refresh_projects()

        messagebox.showinfo(
            "Project created",
            f"Project created:\n\n{project_dir}\n\n"
            f"Engine: {handler.engine_id}\n"
            f"Target language: {target_language}",
        )

    # ------------------------------------------------------------------
    # Open actions
    # ------------------------------------------------------------------

    def open_console(self) -> None:
        project_dir = self.selected_project_dir()

        if project_dir is None:
            return

        try:
            port = self.console_manager.start(project_dir)
        except Exception as exc:
            messagebox.showerror(
                "Failed to open web console",
                str(exc),
            )
            return

        self.root.title(
            f"AutoGame Localizer Launcher - Console: "
            f"http://127.0.0.1:{port}"
        )

    def open_project_folder(self) -> None:
        project_dir = self.selected_project_dir()

        if project_dir is None:
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(project_dir)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(project_dir)])
            else:
                subprocess.Popen(["xdg-open", str(project_dir)])
        except Exception as exc:
            messagebox.showerror(
                "Failed to open folder",
                str(exc),
            )

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def on_close(self) -> None:
        self.console_manager.stop()
        self.root.destroy()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    ensure_workspace()

    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()