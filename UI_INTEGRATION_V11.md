# AutoGame Localizer UI Integration V11

## Scope

V11 continues the integrated UI work without rewriting translation, engine, QA, patch, installer, or provider core logic.

### UI changes
- Shared navigation links no longer use browser default underline styling.
- Developer Console action bar is grouped into Workflow and Release actions.
- Console task activity now exposes task progress when backend logs contain `Progress x/y`.
- Re-entering a Console automatically resumes polling for an existing running Task.
- Translated metric counts only `machine_translated` and `reviewed` entries.
- Existing responsive baseline remains `1000x700` minimum and `1220x860` target.

### Runtime / service changes
- `phase_ui3_launcher.wait_for_server()` waits for Uvicorn's actual `server.started` state rather than treating a listening port as proof that the newly spawned server started successfully.
- Service Manager and Portal startup both use the new readiness check.
- Existing `phase_*` files remain in place as service/API implementations and fallback baseline.

## Tests

Run from the repository root:

```bash
pytest -q
python ui_integration_smoke_test.py
python ui_runtime_test.py
python service_integration_test.py
python lifecycle_integration_test.py
```

Expected V11 result in the current uploaded repository:

- `19 passed` existing tests
- UI integration smoke: PASS
- UI runtime: PASS
- Service integration: PASS
- Full lifecycle integration: PASS

## Full lifecycle covered

```text
Create Project
  -> Open Console
  -> Scan
  -> Translate (mock provider)
  -> QA
  -> Generate Patch
  -> Install + Backup
  -> Rollback + Restore
```

The lifecycle test uses a temporary RPG Maker MV/MZ fixture and the built-in mock provider. It does not require an external API key.

## Files added / changed by V11

Changed:
- `app_main.py` (existing unified entry)
- `phase_ui3_launcher.py`
- `ui_pages.py`

Added / kept as integration tests:
- `ui_integration.py`
- `ui_integration_smoke_test.py`
- `ui_runtime_test.py`
- `service_integration_test.py`
- `lifecycle_integration_test.py`

Original phase files are intentionally retained:
- `phase_ui1_simple.py`
- `phase_ui2_desktop.py`
- `phase_ui3_launcher.py`
- `phase11_workspace.py`
- `phase12_settings.py`
- `phase75_web.py`

## Secrets / local state

Do not commit or distribute local `.env`, `user_settings.json`, `.cache/`, `projects/`, `build/`, `dist/`, or `.venv/` contents as part of a clean source release.
