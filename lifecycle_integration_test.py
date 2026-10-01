from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

import http.client
import uvicorn


def free_port(start: int = 8800) -> int:
    port = start
    while port < start + 1000:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("No free port")


def request(port: int, method: str, path: str, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        text = raw.decode("utf-8", errors="replace")
        data = None
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
        return r.status, data
    finally:
        conn.close()


def wait_task(port: int, task_id: str, api_prefix: str = "/api", timeout: float = 20.0):
    end = time.time() + timeout
    while time.time() < end:
        status, data = request(port, "GET", f"{api_prefix}/tasks/{task_id}")
        assert status == 200, data
        if data["status"] in {"completed", "failed"}:
            return data
        time.sleep(0.12)
    raise AssertionError(f"task {task_id} timeout")


def build_game(root: Path) -> None:
    data = root / "data"
    data.mkdir(parents=True)
    (data / "Items.json").write_text(json.dumps([
        None,
        {"id": 1, "name": "Potion", "description": "Restores HP"},
    ]), encoding="utf-8")
    (data / "Map001.json").write_text(json.dumps({
        "events": [None, {"pages": [{"list": [
            {"code": 401, "indent": 0, "parameters": ["Hello \\v[1]"]},
            {"code": 102, "indent": 0, "parameters": [["Yes", "No"]]},
        ]}]}]
    }), encoding="utf-8")


def main() -> None:
    original_cwd = os.getcwd()
    original_projects_root = os.environ.get("AGL_PROJECTS_ROOT")
    original_portal_url = os.environ.get("AGL_PORTAL_URL")

    with tempfile.TemporaryDirectory(prefix="agl-lifecycle-") as tmp:
        root = Path(tmp)
        game = root / "DemoGame"
        build_game(game)
        projects = root / "projects"
        projects.mkdir()
        (root / "config.toml").write_text(
            "[translation]\n"
            "target_language = \"zh-CN\"\n\n"
            "[project]\n"
            "cache_db = \".cache/translation.db\"\n\n"
            "[providers.mock]\n"
            "type = \"mock\"\n",
            encoding="utf-8",
        )

        os.environ["AGL_PROJECTS_ROOT"] = str(projects)
        os.environ["AGL_PORTAL_URL"] = "http://127.0.0.1:8899/"
        os.chdir(root)

        import phase11_workspace
        import phase_ui3_launcher

        app = phase11_workspace.create_workspace_app()
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert phase_ui3_launcher.wait_for_server(server, thread)

        try:
            status, data = request(port, "POST", "/api/projects/create", {
                "name": "DemoGame_zh",
                "game_path": str(game),
                "target_language": "zh-CN",
            })
            assert status == 200, data

            status, data = request(port, "POST", "/api/projects/open", {"name": "DemoGame_zh"})
            assert status == 200, data
            console_port = port
            console_api = data["url"].rstrip("/") + "/api"

            status, data = request(console_port, "GET", f"{console_api}/meta")
            assert status == 200, data

            status, data = request(console_port, "POST", f"{console_api}/actions/scan", {})
            assert status == 200, data
            task = wait_task(console_port, data["task_id"], console_api)
            assert task["status"] == "completed", task

            status, data = request(console_port, "GET", f"{console_api}/entries?limit=1000")
            assert status == 200 and data["total"] >= 5, data

            status, data = request(console_port, "POST", f"{console_api}/actions/translate", {
                "provider": "mock",
                "retranslate": False,
                "no_cache": True,
            })
            assert status == 200, data
            task = wait_task(console_port, data["task_id"], console_api)
            assert task["status"] == "completed", task

            status, entries = request(console_port, "GET", f"{console_api}/entries?limit=1000")
            assert status == 200 and all(e["target_text"] for e in entries["items"]), entries

            status, data = request(console_port, "POST", f"{console_api}/actions/qa", {"apply": True, "apply_warnings": True})
            assert status == 200, data
            task = wait_task(console_port, data["task_id"], console_api)
            assert task["status"] == "completed", task

            status, data = request(console_port, "POST", f"{console_api}/actions/patch", {})
            assert status == 200, data
            task = wait_task(console_port, data["task_id"], console_api)
            assert task["status"] == "completed", task

            status, patches = request(console_port, "GET", f"{console_api}/patches")
            assert status == 200 and patches["items"], patches
            patch_name = patches["items"][0]["name"]

            original = (game / "data" / "Items.json").read_text(encoding="utf-8")

            status, data = request(console_port, "POST", f"{console_api}/actions/install", {
                "patch_path": patch_name,
                "force": False,
                "backup": True,
            })
            assert status == 200, data
            task = wait_task(console_port, data["task_id"], console_api)
            assert task["status"] == "completed", task
            installed = (game / "data" / "Items.json").read_text(encoding="utf-8")
            assert installed != original, "install should modify the game file"

            status, backups = request(console_port, "GET", f"{console_api}/backups")
            assert status == 200 and backups["items"], backups
            backup_id = backups["items"][0]["backup_id"]

            status, data = request(console_port, "POST", f"{console_api}/actions/rollback", {
                "backup_id": backup_id,
                "force": False,
            })
            assert status == 200, data
            task = wait_task(console_port, data["task_id"], console_api)
            assert task["status"] == "completed", task
            restored = (game / "data" / "Items.json").read_text(encoding="utf-8")
            assert restored == original, "rollback should restore the original game file"

            print("Lifecycle integration test: PASS")
            print("- Create project: PASS")
            print("- Open console: PASS")
            print("- Scan: PASS")
            print("- Translate with mock provider: PASS")
            print("- QA: PASS")
            print("- Generate patch: PASS")
            print("- Install + backup: PASS")
            print("- Rollback + restore: PASS")
        finally:
            # Project consoles share the workspace server and stop with it.
            server.should_exit = True
            thread.join(timeout=5)
            if thread.is_alive():
                raise AssertionError("workspace server did not stop within 5 seconds")

            # IMPORTANT: restore cwd before TemporaryDirectory.__exit__ removes the temp tree.
            # On Windows, deleting the current working directory causes shutil/tempfile
            # cleanup to recurse and can explode into a huge RecursionError traceback.
            os.chdir(original_cwd)
            if original_projects_root is None:
                os.environ.pop("AGL_PROJECTS_ROOT", None)
            else:
                os.environ["AGL_PROJECTS_ROOT"] = original_projects_root
            if original_portal_url is None:
                os.environ.pop("AGL_PORTAL_URL", None)
            else:
                os.environ["AGL_PORTAL_URL"] = original_portal_url


def test_lifecycle_integration() -> None:
    main()


if __name__ == "__main__":
    main()
