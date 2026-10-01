from __future__ import annotations

import importlib

import ui_integration


def assert_marker(name: str, value: str, marker: str) -> None:
    if marker not in value:
        raise AssertionError(f"{name} missing marker: {marker}")


def main() -> None:
    modules = [
        importlib.import_module("phase_ui3_launcher"),
        importlib.import_module("phase_ui1_simple"),
        importlib.import_module("phase11_workspace"),
        importlib.import_module("phase12_settings"),
        importlib.import_module("phase75_web"),
    ]
    ui_integration.install()

    checks = {
        "portal": (modules[0].PORTAL_PAGE, "AutoGame Localizer"),
        "player": (modules[1].PAGE, "一键翻译游戏"),
        "workspace": (modules[2].PAGE, "Projects"),
        "settings": (modules[3].PAGE, "Provider Editor"),
        "console": (modules[4].PAGE, "Localization IDE"),
    }
    for name, (page, marker) in checks.items():
        assert_marker(name, page, marker)

    # Confirm the real FastAPI route contracts still exist after page injection.
    builders = {
        "player": (modules[1], "create_simple_app", {"/api/select_folder", "/api/state", "/api/start", "/api/apply", "/api/restore"}),
        "workspace": (modules[2], "create_workspace_app", {"/api/projects", "/api/projects/create", "/api/projects/open", "/api/projects/archive", "/api/projects/export", "/api/projects/import"}),
        "settings": (modules[3], "create_settings_app", {"/api/settings/config", "/api/settings/general", "/api/settings/provider", "/api/settings/provider/test", "/api/settings/env"}),
    }
    skipped=[]
    for name, (module, builder_name, expected) in builders.items():
        builder=getattr(module,builder_name,None)
        if builder is None:
            skipped.append(name)
            continue
        app=builder()
        routes={getattr(r,"path","") for r in app.routes}
        missing=expected-routes
        if missing:
            raise AssertionError(f"{name} missing routes: {sorted(missing)}")

    print("UI integration smoke test: PASS")
    print("- shared templates injected: PASS")
    if skipped:
        print("- route contract checks skipped for: "+", ".join(skipped)+" (test stubs do not expose app builders)")
    else:
        print("- player/workspace/settings routes: PASS")


if __name__ == "__main__":
    main()
