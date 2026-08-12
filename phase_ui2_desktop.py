from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from typing import Any, Optional

try:
    import webview
except ImportError:
    sys.exit(
        "pywebview is not installed.\n"
        "Please run: pip install pywebview"
    )

import uvicorn
from fastapi.responses import HTMLResponse

from phase_ui1_simple import create_simple_app


# ----------------------------------------------------------------------
# Desktop page
# ----------------------------------------------------------------------

DESKTOP_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer Desktop</title>
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
      background: #2563eb;
      color: #fff;
      cursor: pointer;
    }

    #toolbar button:hover {
      background: #1d4ed8;
    }

    #status {
      color: #475569;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 720px;
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
    <button onclick="chooseFolder()">选择游戏文件夹</button>
    <div id="status">桌面模式启动中...</div>
  </div>

  <iframe id="frame" src="/"></iframe>

  <script>
    function setStatus(message) {
      document.getElementById("status").textContent = message;
    }

    async function chooseFolder() {
      try {
        if (!window.pywebview || !window.pywebview.api) {
          setStatus("桌面对话框未就绪。");
          return;
        }

        const path = await window.pywebview.api.select_folder();

        if (!path) {
          setStatus("未选择文件夹。");
          return;
        }

        const frame = document.getElementById("frame");
        const doc = frame.contentDocument;

        if (!doc) {
          setStatus("无法访问内部页面。");
          return;
        }

        const input = doc.getElementById("game_path");

        if (!input) {
          setStatus("找不到游戏路径输入框。");
          return;
        }

        input.value = path;
        setStatus("已选择：" + path);

      } catch (err) {
        setStatus("选择文件夹失败：" + err);
      }
    }

    window.addEventListener("pywebviewready", function () {
      setStatus("桌面模式就绪。");
    });
  </script>
</body>
</html>
"""


# ----------------------------------------------------------------------
# Network helpers
# ----------------------------------------------------------------------

def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start_port: int = 8300, max_attempts: int = 200) -> int:
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


def resolve_port(preferred_port: int) -> int:
    if not is_port_open(preferred_port):
        return preferred_port

    return find_free_port(preferred_port + 1)


# ----------------------------------------------------------------------
# Desktop app
# ----------------------------------------------------------------------

def create_desktop_app():
    """
    Reuse UI Phase 1 simple app and add /desktop page.
    """
    app = create_simple_app()

    @app.get("/desktop", response_class=HTMLResponse)
    def desktop_page() -> str:
        return DESKTOP_PAGE

    return app


# ----------------------------------------------------------------------
# pywebview API
# ----------------------------------------------------------------------

WINDOW: Optional[Any] = None


class DesktopApi:
    """
    API exposed to JavaScript as window.pywebview.api
    """

    def select_folder(self) -> str:
        global WINDOW

        if WINDOW is None:
            return ""

        try:
            result = WINDOW.create_file_dialog(webview.FOLDER_DIALOG)

            if not result:
                return ""

            if isinstance(result, (list, tuple)):
                if len(result) == 0:
                    return ""
                return str(result[0])

            return str(result)

        except Exception as exc:
            print(f"Folder dialog error: {exc}")
            return ""


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    global WINDOW

    parser = argparse.ArgumentParser(
        description="UI Phase 2: desktop window using pywebview"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8300,
        help="Preferred local port",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview debug/devtools if supported",
    )

    args = parser.parse_args()

    port = resolve_port(args.port)

    app = create_desktop_app()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )

    server = uvicorn.Server(config)

    server_thread = threading.Thread(
        target=server.run,
        daemon=True,
    )

    server_thread.start()

    if not wait_for_port(port):
        server.should_exit = True
        sys.exit("Local web server did not start in time.")

    url = f"http://127.0.0.1:{port}/desktop"

    print("Opening desktop window...")
    print(f"Local server: http://127.0.0.1:{port}")
    print(f"Desktop page: {url}")

    api = DesktopApi()

    WINDOW = webview.create_window(
        title="AutoGame Localizer",
        url=url,
        js_api=api,
        width=1180,
        height=820,
        min_size=(960, 640),
    )

    webview.start(debug=args.debug)

    # When window is closed, stop local server.
    server.should_exit = True
    server_thread.join(timeout=3)


if __name__ == "__main__":
    main()