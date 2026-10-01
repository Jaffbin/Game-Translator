# AutoGame Localizer — UI Integration

This package integrates the redesigned UI into the current `Game-Translator` main branch without changing the translation / project operation modules.

## Files to add / replace

Copy these files into the repository root:

- `ui_pages.py` — shared UI design system + P1–P5 page templates
- `ui_integration.py` — compatibility shim for the canonical `ui.integration` adapter
- `app_main.py` — replacement unified desktop entry point

## Runtime behavior

- Launching `python app_main.py` opens the canonical single-server `agl.api.launcher` desktop shell.
- Existing direct-console usage with command-line arguments is preserved through `agl.api.project_console`.
- Existing FastAPI endpoints remain the source of truth for translation, projects, entries, QA, patch, install and rollback.

## Current API mapping

### Player mode

- `/api/select_folder`
- `/api/start`
- `/api/state`
- `/api/apply`
- `/api/restore`

### Workspace

- `/api/projects`
- `/api/projects/create`
- `/api/projects/open`
- `/api/projects/archive`
- `/api/projects/export`
- `/api/projects/import`

### Project Console

- `/api/meta`
- `/api/entries`
- `/api/entries/update`
- `/api/actions/scan`
- `/api/actions/translate`
- `/api/actions/qa`
- `/api/actions/patch`
- `/api/actions/install`
- `/api/actions/rollback`
- `/api/tasks/*`
- `/api/patches`
- `/api/backups`
- `/api/patch/preview`

### Settings

- `/api/settings/config`
- `/api/settings/general`
- `/api/settings/provider`
- `/api/settings/provider/test`
- `/api/settings/env`

## Important limitations

The redesigned UI exposes positions for Glossary and Translation Memory, but the current backend does not expose dedicated APIs for these features. Those panels therefore remain explicit placeholders rather than pretending that persistence already exists.

The API key is entered as a password field and is only sent to the existing environment-variable endpoint; the UI never renders the stored secret.

## Verification

- Python compilation check: passed
- Rendered page JavaScript syntax checks with Node.js: passed
- Minimum viewport requirement retained at `1000px`
- Desktop window sizing comes from `agl.api.launcher`: `1220 × 860`, minimum `1000 × 700`
