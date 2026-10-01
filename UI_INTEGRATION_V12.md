# AutoGame Localizer UI Integration v12

## What changed
- Unified UI remains in `ui_pages.py` and is injected through `ui_integration.py`.
- Project Console now has functional Glossary CRUD, Translation Memory search, and single-entry AI/TM suggestions.
- Batch translation now passes the project glossary into the provider prompt.
- Single-entry suggestion prefers an exact Translation Memory match before calling the configured provider, unless `force_ai` is requested.
- Existing phase files remain in place as compatibility/fallback baselines.

## New API routes
- `GET /api/glossary?q=`
- `POST /api/glossary`
- `DELETE /api/glossary?source_term=`
- `GET /api/translation-memory?q=&language=&limit=`
- `POST /api/entries/suggest`

## Tests
- 21 pytest tests passing.
- Existing UI smoke/runtime/service/lifecycle tests passing.
- New feature coverage: `test_v12_features.py`.

## Sensitive files intentionally excluded from clean packages
- `.env`
- `config.toml`
- `user_settings.json`
- `.cache/`
- `.venv/`
- local `projects/`
- `.git/`
