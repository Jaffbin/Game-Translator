from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time

from agl import operations
from agl.models import EntryStatus
from agl.project import ProjectStore
from fastapi.testclient import TestClient
from phase75_web import create_app


def make_game(root: Path) -> tuple[Path, Path]:
    game = root / 'Game'; (game / 'data').mkdir(parents=True)
    path = game / 'data' / 'Map001.json'
    path.write_text(json.dumps({
        'events':[None, {'pages':[{'list':[
            {'code':401,'indent':0,'parameters':['Hello']},
            {'code':401,'indent':0,'parameters':['World']},
        ]}]}]
    }), encoding='utf-8')
    project = root / 'Project'
    ProjectStore.create(project, game, 'rpgmaker_mv_mz', 'zh-CN', 'P').close()
    operations.scan_project(project)
    return project, path


def wait_task(client: TestClient, task_id: str, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f'/api/tasks/{task_id}').json()
        if data.get('status') != 'running':
            return data
        time.sleep(0.03)
    raise AssertionError('task timeout')


def test_sync_apply_keep_retranslate_ignore_and_add():
    with TemporaryDirectory() as td:
        root = Path(td)
        project, path = make_game(root)
        with ProjectStore(project) as store:
            hello = next(e for e in store.all_entries() if e.source_text == 'Hello')
            store.update_entry(hello.id, target_text='你好', status=EntryStatus.REVIEWED, human_reviewed=True)

        path.write_text(json.dumps({
            'events':[None, {'pages':[{'list':[
                {'code':401,'indent':0,'parameters':['Hello world']},
                {'code':401,'indent':0,'parameters':['World']},
                {'code':401,'indent':0,'parameters':['New text']},
            ]}]}]
        }), encoding='utf-8')
        preview = operations.preview_update_sync(project)
        assert preview['summary']['changed'] == 1
        assert preview['summary']['added'] == 1
        changed = next(x for x in preview['items'] if x['kind'] == 'changed')
        added = next(x for x in preview['items'] if x['kind'] == 'added')

        result = operations.apply_update_sync(project, [
            {'origin_key': changed['origin_key'], 'action': 'keep'},
            {'origin_key': added['origin_key'], 'action': 'keep'},
        ])
        assert result['changed'] == 1 and result['added'] == 1
        assert result['checkpoint']
        with ProjectStore(project) as store:
            updated = store.get_entry(changed['entry_id'])
            assert updated.source_text == 'Hello world'
            assert updated.target_text == '你好'
            assert updated.status == EntryStatus.NEEDS_REVIEW
            assert updated.fuzzy is True
            assert any(e.source_text == 'New text' for e in store.all_entries())

        path.write_text(json.dumps({
            'events':[None, {'pages':[{'list':[
                {'code':401,'indent':0,'parameters':['Hello final']},
                {'code':401,'indent':0,'parameters':['World']},
                {'code':401,'indent':0,'parameters':['New text']},
            ]}]}]
        }), encoding='utf-8')
        preview = operations.preview_update_sync(project)
        changed = next(x for x in preview['items'] if x['kind'] == 'changed')
        result = operations.apply_update_sync(project, [{'origin_key': changed['origin_key'], 'action': 'retranslate'}])
        assert result['retranslated'] == 1
        with ProjectStore(project) as store:
            updated = store.get_entry(changed['entry_id'])
            assert updated.source_text == 'Hello final'
            assert updated.target_text == ''
            assert updated.status == EntryStatus.PENDING

        path.write_text(json.dumps({
            'events':[None, {'pages':[{'list':[
                {'code':401,'indent':0,'parameters':['Hello final']},
                {'code':401,'indent':0,'parameters':['World changed']},
                {'code':401,'indent':0,'parameters':['New text']},
            ]}]}]
        }), encoding='utf-8')
        preview = operations.preview_update_sync(project)
        changed = next(x for x in preview['items'] if x['kind'] == 'changed' and x['source_after'] == 'World changed')
        result = operations.apply_update_sync(project, [{'origin_key': changed['origin_key'], 'action': 'ignore'}])
        assert result['ignored'] == 1
        preview_again = operations.preview_update_sync(project)
        assert preview_again['summary']['changed'] == 0
        assert preview_again['summary']['suppressed'] >= 1


def test_v17_sync_api_and_ui_contract():
    with TemporaryDirectory() as td:
        project, _ = make_game(Path(td))
        app = create_app(project)
        with TestClient(app) as client:
            r = client.post('/api/actions/sync-apply', json={'decisions':[{'origin_key':'missing','action':'keep'}]})
            assert r.status_code == 200
            result = wait_task(client, r.json()['task_id'])
            assert result['status'] == 'completed'
            assert result['result']['skipped'] == 1
        import ui_pages
        html = ui_pages.CONSOLE_PAGE
        for token in ('/api/actions/sync-apply', '保留现有译文', '重新翻译', '应用决定', '加入项目', '保留在项目', '审校'):
            assert token in html
