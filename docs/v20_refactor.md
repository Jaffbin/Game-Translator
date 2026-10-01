# v20 Refactor Notes

## Completed in the current baseline

- `app_main.py` now enters the canonical `agl.api` package.
- Portal, player, workspace, settings, and project consoles share one Uvicorn
  server and one localhost origin.
- Project consoles are mounted dynamically instead of starting one server thread
  and port per project.
- The player workflow has explicit preflight, local scan/estimate, cloud consent,
  QA, patch, install, backup, cancel, and rollback stages.
- Simple mode refuses to use the mock provider as if it were a real translation.
- Scan, translation, quality, delivery, and project-data implementations now live
  in focused `agl.services` modules; `agl.operations` is a compatibility facade.
- New API keys use the Windows credential vault, with `.env` retained as a
  compatibility fallback.
- Project databases have a schema version and create a pre-v2 recovery copy when
  an unversioned database is first migrated.
- Application and patch metadata share version `0.20.0`.
- The former player and launcher implementations now live behind canonical
  compatibility adapters. Their `phase_ui*.py` files are import-only shims.
- UI template installation no longer imports any phase-named module into the
  canonical runtime.

## Compatibility policy

The root `ui_pages.py`, `ui_integration.py`, and `operations.py` modules are
compatibility shims. The `phase*.py` modules remain callable during the v20
deprecation window because existing scripts and tests import them.

New code should import from:

```python
from agl.api import create_desktop_app
from agl.services import PlayerWorkflow
from ui.pages import CONSOLE_PAGE
```

Do not add new dependencies on phase modules outside the compatibility adapters.

## Next migration stage

After a full v20 release cycle, remove obsolete phase experiments and historical
UI patch scripts. Public compatibility shims should only be removed after a
documented deprecation cycle.
