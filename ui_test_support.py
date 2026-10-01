from __future__ import annotations

import os
from pathlib import Path


def browser_executable() -> str:
    candidates = [
        os.getenv("AGL_BROWSER_PATH", ""),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "No supported browser found. Set AGL_BROWSER_PATH to a Chromium-based browser."
    )
