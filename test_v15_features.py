from pathlib import Path
from tempfile import TemporaryDirectory

import phase75_web
from fastapi.testclient import TestClient
from agl.models import EntryStatus, TranslationEntry
from agl.project import ProjectStore


def make_project(tmp: Path) -> Path:
    project = tmp / 'P'; project.mkdir()
    store = ProjectStore.create(project, tmp / 'Game', 'rpgmaker_mv_mz', 'zh-CN', 'P')
    store.upsert_entries([
        TranslationEntry(id='e1', source_text='Open the gate', target_text='打开大门', context='Castle ending dialogue', file_path='Data/A.json', engine='rpgmaker_mv_mz', status=EntryStatus.REVIEWED),
        TranslationEntry(id='e2', source_text='The gate is open', target_text='城门已打开', context='Castle dialogue', file_path='Data/A.json', engine='rpgmaker_mv_mz', status=EntryStatus.MACHINE_TRANSLATED),
    ])
    store.save_meta(); store.close()
    (project / 'glossary.csv').write_text('source_term,target_term,level,case_sensitive\ngate,城门,required,false\n', encoding='utf-8-sig')
    return project


def test_v15_ui_hooks_present():
    import ui_pages
    html = ui_pages.CONSOLE_PAGE
    for token in ('v15-quick-card','v15InsertGlossaryTerm','v15Similarity','window.saveEntry=v15SaveAndRefresh','Ctrl + S 保存','Similar Translation Memory'):
        assert token in html


def test_v15_assistant_context_still_contracts():
    with TemporaryDirectory() as td:
        project = make_project(Path(td))
        app = phase75_web.create_app(project)
        with TestClient(app) as client:
            r = client.get('/api/entries/e1/assistant-context')
            assert r.status_code == 200, r.text
            body = r.json()
            assert body['glossary'][0]['target_term'] == '城门'
            assert body['qa']['passed'] is False
