from __future__ import annotations

import threading

from agl.api.project_console import TaskManager


def test_task_start_check_is_atomic():
    manager = TaskManager()
    barrier = threading.Barrier(3)
    release = threading.Event()
    started = []
    rejected = []

    def launch() -> None:
        barrier.wait()
        try:
            started.append(manager.start("scan", lambda log: release.wait(2)))
        except RuntimeError:
            rejected.append(True)

    threads = [threading.Thread(target=launch), threading.Thread(target=launch)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1)

    assert len(started) == 1
    assert len(rejected) == 1
    release.set()
