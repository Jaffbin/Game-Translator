# GameLocalizer UI Integration V16

## Scope
V16 adds project History, manual Checkpoints/Revisions, Revision Diff, and non-destructive Update Sync preview to the integrated Localization IDE. The existing phase UI modules remain in the repository as backend/fallback baselines.

## Key files
- `app_main.py` — unified desktop entry; preserves legacy project-path CLI behavior.
- `ui_integration.py` — injects unified page templates into the existing phase modules.
- `ui_pages.py` — shared design system and P1-P5 pages.
- `agl/project.py` — entry origin identity, entry history, checkpoints, revision snapshots/diffs.
- `agl/operations.py` — non-destructive game/project sync preview.
- `phase75_web.py` — History, Revisions, Update Sync API routes.

## New APIs
- `GET /api/history?limit=...`
- `GET /api/entries/{entry_id}/history`
- `GET /api/revisions?limit=...`
- `POST /api/revisions` with `{ "label": "..." }`
- `GET /api/revisions/{revision_id}/diff`
- `POST /api/actions/sync-preview`

## UI behavior
- Developer sidebar: `Patch`, `Update Sync`, `History`.
- History displays checkpoints and recent Entry changes.
- Revision Diff compares a checkpoint against the current state.
- Update Sync runs as a background task and reports added/changed/removed/unchanged text without modifying game files.
- When a game's existing source text changes at a stable engine/file/location identity, the existing Entry ID is preserved, its translation is retained, and the Entry becomes `needs_review` with `fuzzy=true`.
- Async Entry Context loading is suppressed while History/Sync tools are active so tool results cannot be overwritten by a late Context request.

## Safety
- Sync Preview is read-only.
- Install/Rollback remain confirmation-protected.
- API keys are never rendered back in plaintext.
- Project revision/history responses do not include API key values.

## Verification
- 29 pytest tests pass.
- UI integration smoke passes.
- Service integration passes.
- Full lifecycle integration passes: Create -> Open -> Scan -> Translate -> QA -> Patch -> Install -> Rollback.
- UI v16 runtime passes at 1000x700.
- All page templates have balanced script tags.
