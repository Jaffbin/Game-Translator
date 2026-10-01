from __future__ import annotations

import http.client
import socket
import threading
import time
from contextlib import closing

import phase_ui3_launcher


def free_port(start: int = 8500) -> int:
    port = start
    while port < start + 1000:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("No free test port available")


def get_json(port: int, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read().decode("utf-8", errors="replace")
        return r.status, body
    finally:
        conn.close()


def main() -> None:
    portal_port = free_port()
    services = phase_ui3_launcher.ServiceManager(f"http://127.0.0.1:{portal_port}/")
    portal_app = phase_ui3_launcher.create_portal_app(services)
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(portal_app, host="127.0.0.1", port=portal_port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        assert phase_ui3_launcher.wait_for_server(server, thread), "portal did not start"
        status, body = get_json(portal_port, "/")
        assert status == 200 and "AutoGame Localizer" in body

        for mode in ("simple", "advanced", "settings"):
            url = services.start(mode)
            host = "127.0.0.1"
            port = int(url.split(":")[2].split("/")[0])
            status, body = get_json(port, "/")
            assert status == 200 and "<!doctype html>" in body.lower()
            assert services.start(mode) == url, f"service {mode} should be reused"

        print("Service integration test: PASS")
        print("- Portal start: PASS")
        print("- simple/advanced/settings service start: PASS")
        print("- service reuse: PASS")
    finally:
        services.stop_all()
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
