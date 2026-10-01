# AutoGame Localizer — UI Integration v15

v15 continues the integrated Localization IDE baseline.

## Editor efficiency
- Inline QA results directly under the translation editor.
- Quick Glossary insertion uses `data-*` attributes + event listeners; no dynamic JSON in HTML `onclick` attributes.
- Similar Translation Memory candidates are ranked locally with Levenshtein similarity.
- AI Suggestion now renders a compact diff against the current translation.
- Save uses `Ctrl+S` and preserves the current entry after refresh.
- Saving reloads assistant context so QA is re-evaluated against the saved translation.
- Existing `Ctrl+Enter`, `Alt+Up/Down`, `Ctrl+Shift+Q` shortcuts remain supported.
- `Ctrl+Shift+G` focuses Glossary; `Ctrl+Shift+M` focuses Translation Memory.

## Backend contract
No translation-core rewrite. v15 reuses existing APIs:
- `GET /api/entries/{entry_id}/assistant-context`
- `POST /api/entries/update`
- `POST /api/entries/suggest`
- `GET /api/glossary`
- `GET /api/translation-memory`
- existing QA / Patch / Task / Install / Rollback endpoints

## Validation
- Python compile: PASS
- Full pytest suite: PASS
- Embedded Console JavaScript syntax: PASS
- Chromium runtime at 1000x700: PASS
- Chromium runtime at 1220x860: PASS
- Existing service integration: PASS
- Existing lifecycle integration: PASS

Keep the original `phase_*.py` files until the new UI has completed another real-machine regression cycle.
