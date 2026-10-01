# AutoGame Localizer UI Integration v5

This bundle is aligned to the current `main` branch of `Jaffbin/Game-Translator` checked on 2026-09-27.

## Replace / add

- Replace `app_main.py` with this bundle's `app_main.py`.
- Add `ui_integration.py` at repository root.
- Add `ui_pages.py` at repository root.

## Runtime

`python app_main.py` starts the existing `phase_ui3_launcher` desktop shell, then the integration layer injects the new P1-P5 HTML templates into the existing FastAPI phase modules.

Command-line direct console behavior remains available through `python app_main.py <project_dir>`.

## v5 integration fixes

- P4 Entries now uses the backend's server-side `q`, `status`, `limit`, and `offset` parameters.
- Entries has status filtering and pagination.
- Entry can be marked Reviewed or locked/unlocked through `/api/entries/update`.
- Patch Preview uses `/api/patch/preview`.
- Install sends the Patch directory **name**, matching the backend's path-component validation; it no longer sends the absolute path returned by `/api/patches`.
- Task polling disables conflicting actions while a task is running and refreshes project/entry state when complete.
- History uses `/api/tasks`.
- Existing translation/project/QA/Patch/Install/Rollback logic is preserved.

## Verification

- Python compile: PASS
- Embedded page JavaScript syntax: PASS (Node 22)
- Runtime UI injection with stub phase modules: PASS
- Viewport floor remains 1000px wide / 700px high in the UI CSS.

## Current backend contracts verified against main

- Player: `/api/select_folder`, `/api/start`, `/api/state`, `/api/apply`, `/api/restore`
- Workspace: `/api/projects`, `/api/projects/create`, `/api/projects/open`, `/api/projects/archive`, `/api/projects/export`, `/api/projects/import`
- Console: `/api/meta`, `/api/entries`, `/api/entries/update`, `/api/actions/*`, `/api/tasks`, `/api/tasks/{task_id}`, `/api/patches`, `/api/backups`, `/api/patch/preview`
- Settings: `/api/settings/config`, `/api/settings/general`, `/api/settings/provider`, `/api/settings/provider/test`, `/api/settings/env`
