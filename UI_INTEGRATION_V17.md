# AutoGame Localizer — UI Integration v17

## What changed

v17 turns Update Sync from a read-only preview into an explicit decision workflow.

### Update Sync decisions

- `keep`
  - Added entry: import into the project as `pending`.
  - Changed entry: accept the new source while preserving the existing translation; mark the entry `needs_review` and `fuzzy`.
  - Removed entry: keep it in the project and remember the decision so the same removal is not shown repeatedly.
- `retranslate`
  - Changed entry only: accept the new source, clear the target translation, and mark it `pending`/`fuzzy`.
- `ignore`
  - Persist an ignore decision keyed by stable `origin_key` + current source fingerprint. The same change is suppressed until the source changes again.
- `review`
  - Added entry: import as `pending` and open it for review.
  - Changed entry: apply the source update as `needs_review` and open it.
  - Removed entry: leave the project entry unchanged and open it.

Before any project-changing sync decision, a `Before Update Sync` checkpoint is created automatically.

No sync-apply operation writes directly to game files. Game installation remains a separate Patch/Install workflow.

## Files to overlay

- `agl/operations.py`
- `phase75_web.py`
- `ui_pages.py`
- `test_v17_features.py`
- `ui_v17_runtime_test.py`
- `UI_INTEGRATION_V17.md`

## Validation

- Python compile: PASS
- Pytest suite: 31 passed
- UI runtime: PASS at 1000×700 and 1220×860
- Service integration: PASS
- Full lifecycle integration: PASS

## Existing files

Keep the existing `phase_ui*.py`, `phase11_workspace.py`, `phase12_settings.py`, `phase75_web.py`, and `agl/` modules. v17 is intentionally additive and does not remove the previous UI baselines.
