# AutoGame Localizer UI Integration v7

This bundle is a UI-layer integration baseline for the current `main` branch.

## Keep the original phase files

Do **not** delete the original `phase_*.py` files. They still provide the backend services and are used as the fallback/reference implementation.

Copy these files to the repository root:

- `app_main.py`
- `ui_integration.py`
- `ui_pages.py`
- `ui_integration_smoke_test.py`

## What v7 fixes

- Player mode now handles the backend `warning` state (`未找到可翻译文本`) instead of polling forever.
- Player folder reselection resets the player state cleanly.
- Project metrics and entry pagination now use separate totals.
- Keyboard navigation no longer falsely marks an entry dirty when the textarea is updated programmatically.
- API key save rejects an empty secret value.
- Editor-first P4 remains the primary developer workspace.
- Existing backend API contracts remain unchanged.

## Smoke test

From the repository root after copying the files:

```bash
python ui_integration_smoke_test.py
```

The smoke test verifies template injection and the current player/workspace/settings FastAPI route contracts.

## Run

```bash
python app_main.py
```

This opens the integrated pywebview launcher when `webview` is installed, with the existing `1220x860` window and `1000x700` minimum.

For direct Console compatibility, a project path can still be passed as the first positional argument:

```bash
python app_main.py projects/MyGame_zh
```
