from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

import uvicorn

from .desktop import create_desktop_app
from agl.legacy_migration import migrate_legacy_packaged_data

try:
    import webview
except ImportError:
    webview = None


def _is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 200):
        if not _is_port_open(port):
            return port
    raise RuntimeError("No local port is available for the desktop service.")


def _wait_started(server: uvicorn.Server, thread: threading.Thread, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return True
        if not thread.is_alive() or getattr(server, "should_exit", False):
            return False
        time.sleep(0.05)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoGame Localizer desktop")
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    args = parser.parse_args()

    migrate_legacy_packaged_data()
    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(
        uvicorn.Config(create_desktop_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_started(server, thread):
        server.should_exit = True
        thread.join(timeout=1)
        sys.exit("Desktop service did not start in time.")

    if webview is not None and not args.no_window:
        webview.create_window(
            "AutoGame Localizer", url=url, width=1220, height=860, min_size=(1000, 700)
        )
        webview.start(debug=args.debug)
    else:
        print(f"AutoGame Localizer: {url}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    server.should_exit = True
    thread.join(timeout=3)


if __name__ == "__main__":
    main()
