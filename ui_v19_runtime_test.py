from pathlib import Path
from urllib.parse import urlparse
import importlib.util, json
from playwright.sync_api import sync_playwright
from ui_test_support import browser_executable

ROOT = Path(__file__).resolve().parent


def load_html():
    spec = importlib.util.spec_from_file_location('ui_pages_v19_runtime', ROOT / 'ui_pages.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CONSOLE_PAGE


def route_api(route):
    path = urlparse(route.request.url).path
    method = route.request.method
    def ok(payload, status=200):
        route.fulfill(status=status, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
    if path.endswith('/api/meta'):
        return ok({'meta': {'name':'Demo','source_language':'English','target_language':'zh-CN'}, 'stats': {'pending':0,'machine_translated':1,'needs_review':1,'reviewed':1,'locked':0,'error':0}})
    if path.endswith('/api/entries'):
        return ok({'total':2,'items':[
            {'id':'e1','source_text':'Open the gate','target_text':'打开大门','context':'Castle ending','file_path':'Data/A.json','note':'Use glossary','status':'needs_review','locked':False,'ignored':False},
            {'id':'e2','source_text':'Hello','target_text':'你好','context':'Intro','file_path':'Data/A.json','note':'','status':'reviewed','locked':False,'ignored':False},
        ]})
    if path.endswith('/api/entries/e1/assistant-context'):
        return ok({'entry':{'id':'e1','source_text':'Open the gate','target_text':'打开大门','context':'Castle ending','file_path':'Data/A.json','engine':'rpgmaker','location':{'line':1},'note':'Use glossary','status':'needs_review','locked':False,'ignored':False},'glossary':[{'source_term':'gate','target_term':'城门','level':'required'}],'translation_memory':{'exact':[],'matches':[{'source_text':'Open the gates','translated_text':'打开城门','target_language':'zh-CN','human_reviewed':True}]},'qa':{'passed':False,'issues':[{'level':'warning','code':'glossary_missing','message':'gate → 城门'}]},'neighbors':[]})
    if path.endswith('/api/tasks'):
        return ok({'items':[]})
    if path.endswith('/api/entries/update') and method == 'POST':
        return ok({'updated':True})
    return ok({'items':[]})


def run(width):
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path=browser_executable(), args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':width,'height':700})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/**', route_api)
        html=load_html().replace('<head>','<head><base href="http://app.test/">',1)
        page.set_content(html)
        page.wait_for_timeout(900)
        assert page.locator('#entryId').inner_text()=='e1'
        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
        assert page.locator('#networkProgress').count()==1
        assert page.evaluate('getComputedStyle(document.body).fontSize')=='13px'
        page.locator('#targetText').fill('未保存的译文')
        page.get_by_role('button', name='Restore saved').click()
        page.wait_for_timeout(50)
        assert page.locator('#confirmBackdrop').get_attribute('class') == 'modal-backdrop open'
        assert page.locator('#confirmBtn').evaluate('(e)=>document.activeElement===e')
        page.keyboard.press('Escape')
        assert 'open' not in (page.locator('#confirmBackdrop').get_attribute('class') or '')
        assert not errors, errors
        browser.close()


def main():
    run(1000)
    run(1220)
    print('UI v19 runtime test: PASS')


if __name__=='__main__':
    main()
