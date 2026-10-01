"""Compatibility facade for the pre-v20 operations API.

Implementations now live in focused modules under :mod:`agl.services`. Existing
scripts may continue importing ``agl.operations`` without changing behavior.
"""

from .services.delivery import (
    install_project,
    list_backups_project,
    list_patches,
    patch_project,
    preview_patch_project,
    rollback_project,
)
from .services.project_data import export_project_csv, get_project_info, import_project_csv
from .services.quality import (
    delete_project_glossary,
    list_project_glossary,
    run_qa_project,
    upsert_project_glossary,
)
from .services.scan import apply_update_sync, preview_update_sync, scan_project
from .config import load_config
from .providers import create_provider
from .services import translation as _translation
from .services.translation import (
    get_provider_configs,
    remember_reviewed_translations as _remember_reviewed_translations,
    search_translation_memory,
    suggest_entry_translation,
)


def translate_project(*args, **kwargs):
    """Compatibility wrapper preserving legacy dependency monkeypatch points."""
    _translation.load_config = load_config
    _translation.create_provider = create_provider
    return _translation.translate_project(*args, **kwargs)


def remember_reviewed_translations(*args, **kwargs):
    """Compatibility wrapper using the same configurable cache location."""
    _translation.load_config = load_config
    return _remember_reviewed_translations(*args, **kwargs)

__all__ = [
    "get_project_info",
    "scan_project",
    "preview_update_sync",
    "apply_update_sync",
    "translate_project",
    "run_qa_project",
    "list_patches",
    "patch_project",
    "install_project",
    "rollback_project",
    "list_backups_project",
    "export_project_csv",
    "import_project_csv",
    "list_project_glossary",
    "upsert_project_glossary",
    "delete_project_glossary",
    "search_translation_memory",
    "suggest_entry_translation",
    "get_provider_configs",
    "remember_reviewed_translations",
    "preview_patch_project",
]
