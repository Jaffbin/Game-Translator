# AutoGame Localizer — UI Integration v8

This package is an incremental UI integration layer. Keep the existing phase files and backend implementation unchanged.

## v8 changes
- Shared `COMMON_JS` is loaded before page-specific scripts, fixing a real initialization-order issue where page scripts could call `api()` before the shared API helper existed.
- Theme cookie read/write is guarded so the UI remains usable in restricted WebView/document contexts.
- Global status pill now reflects player translation and developer task state.
- Player Apply/Restore states propagate to the global status.
- P4 Entry navigator is no longer a duplicate mini-sidebar; the global Developer Sidebar remains the primary tool navigation.
- Entry list has an explicit empty state and total-count scope.
- Workspace ZIP import uses a custom picker presentation instead of the browser's raw file input UI.
- Existing backend contracts remain unchanged.

## Files
- `app_main.py`
- `ui_integration.py`
- `ui_pages.py`
- `ui_integration_smoke_test.py`
- `ui_runtime_test.py`

## Validation
Run:

```bash
python -m py_compile *.py
python ui_runtime_test.py
python ui_integration_smoke_test.py
```

`ui_runtime_test.py` uses a browser harness with deterministic fake API responses to validate:
- no horizontal overflow at 1000x700 and 1220x860;
- Player folder selection -> start -> completed flow;
- Console Entry editing -> Save flow.

The original `phase_*.py` files remain required for the backend/API implementation and are intentionally not deleted.
