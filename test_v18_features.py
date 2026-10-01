from pathlib import Path
import importlib.util


def load_ui_module():
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('ui_pages_v18_contract', root / 'ui_pages.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_v18_editor_productivity_tokens():
    ui = load_ui_module()
    html = ui.CONSOLE_PAGE
    for token in (
        'QA this entry',
        'Best TM',
        'Apply glossary',
        'Restore saved',
        'bulkRecheckQa()',
        'recheckCurrentEntry()',
        'useBestMemory()',
        'restoreSavedTranslation()',
        'Ctrl + Shift + R',
    ):
        assert token in html


def test_v18_keeps_existing_assistant_and_sync_layers():
    ui = load_ui_module()
    html = ui.CONSOLE_PAGE
    for token in (
        '/api/entries/'+"'+encodeURIComponent(e.id)+'/assistant-context",
        'Update Sync',
        'History & Revisions',
        'Translation Memory',
        'Glossary',
        'AI Suggestion',
    ):
        assert token in html
