# AutoGame Localizer UI Integration v14

## Scope
v14 extends the v13 integrated UI with a context-aware Translation Editor assistant panel.

## Main changes
- Added `GET /api/entries/{entry_id}/assistant-context`.
- Returns only non-secret editor context: entry metadata, relative file path, engine/location, note, matched glossary terms, QA issues, Translation Memory matches, and same-file neighbors.
- Translation Editor automatically loads the context bundle when an entry is selected.
- Context panel now shows Glossary, exact Translation Memory match, QA issues, and same-file context together.
- Exact Translation Memory matches can be inserted into the editor without calling AI.
- AI Suggestion remains user-applied; suggestions never overwrite entries automatically.
- Existing Glossary/TM/AI/QA/Patch/Install/Rollback flows remain unchanged.
- No API keys or secret values are returned by the new endpoint.

## Validation
- `pytest -q`: 24 passed, 2 existing Pydantic v1 deprecation warnings.
- `python ui_runtime_test.py`: PASS.
- `python service_integration_test.py`: PASS.
- `python lifecycle_integration_test.py`: PASS.
- Embedded JavaScript syntax for all 5 page templates: PASS.
- Responsive runtime at 1000x700 and 1220x860: PASS.

## Files changed for v14
- `phase75_web.py`
- `ui_pages.py`
- `test_v14_features.py`
- `ui_runtime_test.py`

Existing `phase_*` files and `agl/` core modules remain in place.
