from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from ui.pages import PLAYER_PAGE, PORTAL_PAGE


def browser_executable() -> str:
    candidates = [
        os.getenv("AGL_BROWSER_PATH", ""),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("No supported browser executable found for runtime UI test.")


def main() -> None:
    errors: list[str] = []
    runtime = {"translated": False}

    def route_api(route):
        path = urlparse(route.request.url).path
        payload = {"ok": True}
        if path.endswith("/api/select_folder"):
            payload = {"path": r"C:\Games\Demo"}
        elif path.endswith("/api/preflight"):
            payload = {
                "path": r"C:\Games\Demo",
                "engine": "rpgmaker_mv_mz",
                "provider": "deepseek",
                "provider_ready": True,
                "diagnostics": {
                    "runtime": {"markers": ["RPG Maker MZ"]},
                    "encoding": {"counts": {"utf-8": 2}},
                    "fonts": {"total": 1},
                    "issues": [{"severity": "warning", "message": "请在游戏内检查字体。"}],
                    "can_scan": True,
                },
            }
        elif path.endswith("/api/recovery"):
            payload = {"project": "Demo_zh-CN", "backup_count": 1}
        elif path.endswith("/api/preferences"):
            payload = {"target_language": "ja"}
        elif path.endswith("/api/readiness"):
            payload = {
                "provider_ready": False,
                "provider": None,
                "supported_engines": ["RPG Maker MV/MZ", "Ren'Py", "Unity text assets"],
            }
        elif path.endswith("/api/settings"):
            payload = {"first_run_completed": True, "ui_mode": "simple"}
        elif path.endswith("/api/auto"):
            runtime["translated"] = True
        elif path.endswith("/api/state"):
            if runtime["translated"]:
                payload = {
                    "state": "needs_attention",
                    "step": "需要处理",
                    "message": "有 1 条翻译失败。",
                    "logs": ["translation failed"],
                    "busy": False,
                    "entries": 12,
                }
            else:
                payload = {
                    "state": "prepared",
                    "step": "等待确认",
                    "message": "扫描完成",
                    "logs": ["scan ok"],
                    "busy": False,
                    "entries": 12,
                    "estimated_tokens": 345,
                    "engine": "rpgmaker_mv_mz",
                    "provider": "deepseek",
                }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=browser_executable(),
            args=["--no-sandbox"],
        )
        portal = browser.new_page(viewport={"width": 1100, "height": 820})
        portal.on("pageerror", lambda error: errors.append(str(error)))
        portal.route("**/api/**", route_api)
        portal_html = PORTAL_PAGE.replace("<head>", '<head><base href="http://app.test/">', 1)
        portal.set_content(portal_html)
        portal.wait_for_timeout(150)
        assert portal.locator("#home").is_visible()
        assert portal.locator("#providerReadiness").is_visible()
        portal.close()

        page = browser.new_page(viewport={"width": 1100, "height": 820})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", route_api)
        html = PLAYER_PAGE.replace("<head>", '<head><base href="http://app.test/player/">', 1)
        page.set_content(html)
        page.wait_for_timeout(100)
        assert page.locator("#playerTargetLang").input_value() == "ja"
        page.get_by_role("button", name="选择文件夹").first.click()
        page.wait_for_timeout(100)
        assert page.locator("#gameReady").inner_text() == "rpgmaker_mv_mz"
        assert "RPG Maker MZ" in page.locator("#diagnosticSummary").inner_text()
        assert "检查字体" in page.locator("#diagnosticIssues").inner_text()
        assert page.locator("#recoveryBtn").is_visible()
        start = page.locator("#startBtn")
        assert start.is_enabled()
        start.click()
        page.wait_for_timeout(1000)
        assert page.locator("#statusBox").get_attribute("class") == "player-status state-attention"
        assert "1 条翻译失败" in page.locator("#attentionMessage").inner_text()
        assert page.locator("#playerLogPanel").is_visible()
        assert "translation failed" in page.locator("#playerLogText").inner_text()
        assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        assert not errors, errors
        browser.close()

    print("UI v20 player runtime test: PASS")


if __name__ == "__main__":
    main()
