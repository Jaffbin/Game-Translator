# AutoGame Localizer UI Integration v6

## Legacy files

Keep the original `phase_ui1_simple.py`, `phase_ui3_launcher.py`, `phase11_workspace.py`, `phase12_settings.py`, and `phase75_web.py` during integration and testing. They remain the rollback baseline and still contain the backend/service implementations. Delete or archive them only after end-to-end runtime validation.

## v6 changes

- Unified friendly HTTP error mapping for common 400/403/404/409/network failures.
- Developer sidebar hash navigation now opens the matching IDE tool instead of leaving inert anchors.
- Install and Rollback retain task IDs and use the shared task poller.
- Rollback is blocked before confirmation when no backup exists.
- Translation Editor tracks unsaved changes and warns before switching entries.
- Player Apply / Restore expose transitional state and friendly failures.
- Workspace has loading, empty and retry states.
- Task history is visible in the Activity panel.
- `app_main.py` now routes launcher flags such as `--debug`, `--no-window`, and `--portal-port` to the launcher while still preserving direct Console invocation when a project path is provided first.
- Viewport floor remains 1000x700; text is not proportionally scaled with window size.

## Current backend integration

The UI reuses the repository's existing Project, Entries, Task, QA, Patch, Install, Rollback, Settings and Provider endpoints. The current backend exposes Entries search/filter/pagination, task polling, patch preview, safe patch-name install validation, backup validation, CSV export/import, and Provider/API-key management. The UI does not invent missing Glossary or Translation Memory APIs.
