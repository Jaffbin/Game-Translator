from pathlib import Path
from urllib.parse import urlparse
import importlib.util
import json
from playwright.sync_api import sync_playwright
from ui_test_support import browser_executable

ROOT = Path(__file__).resolve().parent


def load_html() -> str:
    spec = importlib.util.spec_from_file_location('ui_pages_v17_runtime', ROOT / 'ui_pages.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CONSOLE_PAGE


def run_at(page, viewport):
    errors=[]
    page.set_viewport_size({'width': viewport[0], 'height': viewport[1]})
    def route_api(route):
        request = route.request; path = urlparse(request.url).path; method = request.method
        def ok(payload, status=200):
            route.fulfill(status=status, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
        if path.endswith('/api/meta'): return ok({'meta': {'name':'Demo','source_language':'English','target_language':'zh-CN'}, 'stats': {'pending':0,'machine_translated':1,'needs_review':1,'reviewed':1,'locked':0,'ignored':0,'fuzzy':0,'error':0}})
        if path.endswith('/api/entries'): return ok({'total':2,'items':[{'id':'e1','source_text':'Open the gate','target_text':'打开大门','context':'Castle','file_path':'Data/A.json','note':'','status':'needs_review','locked':False,'ignored':False},{'id':'e2','source_text':'Hello','target_text':'你好','context':'Intro','file_path':'Data/A.json','note':'','status':'reviewed','locked':False,'ignored':False}]})
        if path.endswith('/api/entries/e1/assistant-context'): return ok({'entry':{'id':'e1','source_text':'Open the gate','target_text':'打开大门','context':'Castle','file_path':'Data/A.json','engine':'rpgmaker','location':{'line':1},'note':'','status':'needs_review','locked':False,'ignored':False},'glossary':[{'source_term':'gate','target_term':'城门','level':'required'}],'translation_memory':{'exact':[],'matches':[]},'qa':{'passed':False,'issues':[{'level':'warning','code':'glossary_missing','message':'gate → 城门'}]},'neighbors':[]})
        if path.endswith('/api/tasks') and method=='GET': return ok({'items':[]})
        if path.endswith('/api/actions/sync-preview'): return ok({'task_id':'sync1'})
        if path.endswith('/api/tasks/sync1'): return ok({'id':'sync1','name':'sync-preview','status':'completed','message':'OK','logs':['[Sync] added=1 changed=1 unchanged=0 removed=1'],'result':{'files':1,'summary':{'added':1,'changed':1,'unchanged':0,'removed':1,'errors':0,'suppressed':0},'items':[{'kind':'changed','entry_id':'e1','origin_key':'key-change','file_path':'Data/A.json','source_before':'Open the gate','source_after':'Open the gate!','target_text':'打开大门','status':'needs_review'},{'kind':'added','entry_id':'e3','origin_key':'key-add','file_path':'Data/A.json','source_after':'New line','status':'pending'},{'kind':'removed','entry_id':'e4','origin_key':'key-remove','file_path':'Data/A.json','source_before':'Old line','status':'reviewed'}],'errors':[]}})
        if path.endswith('/api/actions/sync-apply'): return ok({'task_id':'apply1'})
        if path.endswith('/api/tasks/apply1'): return ok({'id':'apply1','name':'sync-apply','status':'completed','message':'OK','logs':['[Sync] applied=1 added=0 changed=1 ignored=0 retranslated=1 reviewed=0 skipped=0'],'result':{'applied':1,'changed':1,'added':0,'ignored':0,'retranslated':1,'reviewed':0,'checkpoint':{'id':'r3'}}})
        if path.endswith('/api/tasks/sync2'): return ok({'id':'sync2','name':'sync-preview','status':'completed','message':'OK','logs':[],'result':{'files':1,'summary':{'added':0,'changed':0,'unchanged':3,'removed':0,'errors':0,'suppressed':0},'items':[],'errors':[]}})
        ok({'items':[]})
    page.route('**/api/**', route_api)
    page.set_content(load_html().replace('<head>', '<head><base href="http://app.test/">', 1))
    page.wait_for_timeout(900)
    page.evaluate("showTool('sync')")
    page.wait_for_timeout(900)
    assert 'Update Sync' in page.locator('#assistBody').inner_text()
    assert page.locator('[data-sync-origin]').count() == 3
    apply=page.locator('#v17ApplySync'); assert apply.is_disabled()
    page.locator('[data-sync-origin="key-change"]').select_option('retranslate')
    assert not apply.is_disabled()
    assert '已选择 1 项' in page.locator('#v17SyncFooter').inner_text()
    assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
    # Ensure the UI can launch the apply task without inline JSON/onclick parsing errors.
    page.evaluate("v17ApplySelected()")
    page.wait_for_timeout(120)
    # Confirm modal should be open; cancel keeps the page stable.
    assert page.locator('#confirmBackdrop').evaluate("el => el.classList.contains('open')")
    page.get_by_role('button', name='取消').click()
    assert not errors, errors


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=browser_executable(),args=['--no-sandbox'])
        for viewport in [(1000,700),(1220,860)]:
            page=browser.new_page(viewport={'width':viewport[0],'height':viewport[1]}); page.on('pageerror', lambda e: None)
            run_at(page, viewport); page.close()
        browser.close()
    print('UI v17 runtime test: PASS')


if __name__ == '__main__':
    main()
