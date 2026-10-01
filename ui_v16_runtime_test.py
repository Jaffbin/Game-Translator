from pathlib import Path
from urllib.parse import urlparse
import importlib.util
import json
from playwright.sync_api import sync_playwright
from ui_test_support import browser_executable

ROOT = Path(__file__).resolve().parent


def load_html() -> str:
    spec = importlib.util.spec_from_file_location('ui_pages_v16_runtime', ROOT / 'ui_pages.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CONSOLE_PAGE


def route_api(route):
    request = route.request
    path = urlparse(request.url).path
    method = request.method

    def ok(payload, status=200):
        route.fulfill(
            status=status,
            content_type='application/json',
            body=json.dumps(payload, ensure_ascii=False),
        )

    if path.endswith('/api/meta'):
        return ok({
            'meta': {'name': 'Demo', 'source_language': 'English', 'target_language': 'zh-CN'},
            'stats': {'pending': 0, 'machine_translated': 1, 'needs_review': 1, 'reviewed': 1, 'locked': 0, 'error': 0},
        })
    if path.endswith('/api/entries'):
        return ok({'total': 2, 'items': [
            {'id': 'e1', 'source_text': 'Open the gate', 'target_text': '打开大门', 'context': 'Castle ending', 'file_path': 'Data/A.json', 'note': 'Use glossary', 'status': 'needs_review', 'locked': False, 'ignored': False},
            {'id': 'e2', 'source_text': 'Hello', 'target_text': '你好', 'context': 'Intro', 'file_path': 'Data/A.json', 'note': '', 'status': 'reviewed', 'locked': False, 'ignored': False},
        ]})
    if path.endswith('/api/entries/e1/assistant-context'):
        return ok({
            'entry': {'id': 'e1', 'source_text': 'Open the gate', 'target_text': '打开大门', 'context': 'Castle ending', 'file_path': 'Data/A.json', 'engine': 'rpgmaker', 'location': {'line': 1}, 'note': 'Use glossary', 'status': 'needs_review', 'locked': False, 'ignored': False},
            'glossary': [{'source_term': 'gate', 'target_term': '城门', 'level': 'required'}],
            'translation_memory': {'exact': [], 'matches': [{'source_text': 'Open the gates', 'translated_text': '打开城门', 'target_language': 'zh-CN', 'human_reviewed': True}]},
            'qa': {'passed': False, 'issues': [{'level': 'warning', 'code': 'glossary_missing', 'message': 'gate → 城门'}]},
            'neighbors': [],
        })
    if path.endswith('/api/tasks'):
        return ok({'items': []})
    if path.endswith('/api/revisions'):
        if method == 'POST':
            return ok({'id': 'r2', 'label': 'New checkpoint', 'entry_count': 2})
        return ok({'items': [{'id': 'r1', 'label': 'Before update', 'created_at': '2026-09-27T10:00:00Z', 'entry_count': 2}]})
    if path.endswith('/api/revisions/r1/diff'):
        return ok({'revision': {'id': 'r1', 'label': 'Before update'}, 'summary': {'changed': 1, 'added': 0, 'removed': 0, 'unchanged': 1}, 'items': [{'kind': 'changed', 'entry_id': 'e1', 'target_before': '打开大门', 'target_after': '打开城门', 'status_before': 'needs_review', 'status_after': 'reviewed', 'fields': ['target_text']}]})
    if path.endswith('/api/history'):
        return ok({'items': [{'entry_id': 'e1', 'action': 'entry_updated', 'changed_at': '2026-09-27T10:01:00Z', 'changed_fields': ['target_text', 'status'], 'target_text': '打开城门'}]})
    if path.endswith('/api/actions/sync-preview'):
        return ok({'task_id': 't1'})
    if path.endswith('/api/tasks/t1'):
        return ok({'id': 't1', 'name': 'sync-preview', 'status': 'completed', 'message': 'OK', 'logs': ['[10:00] [Sync] added=0 changed=1 unchanged=1 removed=0'], 'result': {'files': 1, 'summary': {'added': 0, 'changed': 1, 'unchanged': 1, 'removed': 0, 'errors': 0}, 'items': [{'kind': 'changed', 'entry_id': 'e1', 'file_path': 'Data/A.json', 'source_before': 'Open the gate', 'source_after': 'Open the gate!'}], 'errors': []}})
    if path.endswith('/api/entries/update') and method == 'POST':
        return ok({'updated': True})
    ok({'items': []})


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=browser_executable(), args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1000, 'height': 700})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/**', route_api)
        html = load_html().replace('<head>', '<head><base href="http://app.test/">', 1)
        page.set_content(html)
        page.wait_for_timeout(900)

        assert page.locator('#entryId').inner_text() == 'e1'
        assert 'glossary_missing' in page.locator('#v15QaInline').inner_text()
        assert '城门' in page.locator('#assistBody').inner_text()

        page.evaluate("showTool('history')")
        page.wait_for_timeout(120)
        assert 'History & Revisions' in page.locator('#assistBody').inner_text()
        page.get_by_role('button', name='Compare').click()
        page.wait_for_timeout(80)
        assert 'Revision Diff' in page.locator('#assistBody').inner_text()

        page.evaluate("showTool('sync')")
        page.wait_for_timeout(900)
        assert 'Update Sync' in page.locator('#assistBody').inner_text()
        assert 'Changed' in page.locator('#assistBody').inner_text()
        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
        assert not errors, errors
        browser.close()
    print('UI v16 runtime test: PASS')


if __name__ == '__main__':
    main()
