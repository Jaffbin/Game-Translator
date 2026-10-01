from pathlib import Path
from tempfile import TemporaryDirectory
import json

from fastapi.testclient import TestClient

import phase75_web
from agl.models import EntryStatus, TranslationEntry
from agl.project import ProjectStore
from agl import operations


def make_project(tmp: Path) -> Path:
    project = tmp / 'P'; project.mkdir()
    game = tmp / 'Game'; (game / 'data').mkdir(parents=True)
    store = ProjectStore.create(project, game, 'rpgmaker_mv_mz', 'zh-CN', 'P')
    store.upsert_entries([
        TranslationEntry(
            id='e1', source_text='Open the gate', target_text='打开城门', context='Castle',
            file_path='data/Map001.json', engine='rpgmaker_mv_mz',
            location={'type':'json_path','path':['events',1,'pages',0,'list',0,'parameters',0]},
            status=EntryStatus.REVIEWED, human_reviewed=True,
        ),
        TranslationEntry(
            id='e2', source_text='Close the gate', target_text='关闭城门', context='Castle',
            file_path='data/Map001.json', engine='rpgmaker_mv_mz',
            location={'type':'json_path','path':['events',1,'pages',0,'list',1,'parameters',0]},
            status=EntryStatus.MACHINE_TRANSLATED, machine_translated=True,
        ),
    ])
    store.close()
    return project


def test_history_and_revision_diff():
    with TemporaryDirectory() as td:
        project = make_project(Path(td))
        with ProjectStore(project) as store:
            store.update_entry('e1', target_text='打开城门。', status=EntryStatus.REVIEWED, human_reviewed=True)
            hist = store.history('e1', 10)
            assert hist and 'target_text' in hist[0]['changed_fields']
            rev = store.create_revision('Before change')
            store.update_entry('e1', target_text='打开大门。', status=EntryStatus.NEEDS_REVIEW, human_reviewed=False)
            diff = store.revision_diff(rev['id'])
            assert diff['summary']['changed'] == 1
            assert diff['items'][0]['kind'] == 'changed'


def test_sync_preview_and_stable_identity():
    with TemporaryDirectory() as td:
        root = Path(td)
        game = root / 'Game'; (game / 'data').mkdir(parents=True)
        (game / 'data' / 'Map001.json').write_text(json.dumps({
            'events':[None, {'pages':[{'list':[{'code':401,'indent':0,'parameters':['Hello']}, {'code':401,'indent':0,'parameters':['World'] }]}]}]
        }), encoding='utf-8')
        project = root / 'P'
        ProjectStore.create(project, game, 'rpgmaker_mv_mz', 'zh-CN', 'P').close()
        operations.scan_project(project)
        with ProjectStore(project) as store:
            hello = next(e for e in store.all_entries() if e.source_text == 'Hello')
            original_id = hello.id
            store.update_entry(original_id, target_text='你好', status=EntryStatus.REVIEWED, human_reviewed=True)
        (game / 'data' / 'Map001.json').write_text(json.dumps({
            'events':[None, {'pages':[{'list':[{'code':401,'indent':0,'parameters':['Hello world']}, {'code':401,'indent':0,'parameters':['World'] }]}]}]
        }), encoding='utf-8')
        preview = operations.preview_update_sync(project)
        assert preview['summary']['changed'] == 1
        operations.scan_project(project)
        with ProjectStore(project) as store:
            updated = store.get_entry(original_id)
            assert updated is not None
            assert updated.source_text == 'Hello world'
            assert updated.target_text == '你好'
            assert updated.status == EntryStatus.NEEDS_REVIEW
            assert updated.fuzzy is True


def test_v16_api_contracts_and_ui_tokens():
    with TemporaryDirectory() as td:
        project = make_project(Path(td))
        app = phase75_web.create_app(project)
        with TestClient(app) as client:
            r = client.get('/api/revisions'); assert r.status_code == 200
            r = client.post('/api/revisions', json={'label':'Checkpoint A'}); assert r.status_code == 200
            rid = r.json()['id']
            r = client.get(f'/api/revisions/{rid}/diff'); assert r.status_code == 200
            r = client.get('/api/history?limit=20'); assert r.status_code == 200
            r = client.get('/api/entries/e1/history'); assert r.status_code == 200
        import ui_pages
        html = ui_pages.CONSOLE_PAGE
        for token in ('Update Sync','History & Revisions','/api/revisions','/api/actions/sync-preview','Revision Diff','Create checkpoint'):
            assert token in html
