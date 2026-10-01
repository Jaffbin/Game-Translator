from pathlib import Path
from urllib.parse import urlparse
import importlib.util, json
from playwright.sync_api import sync_playwright
from ui_test_support import browser_executable

ROOT = Path(__file__).resolve().parent


def load_html():
    spec = importlib.util.spec_from_file_location('ui_pages_v18_runtime', ROOT / 'ui_pages.py')
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
    if path.endswith('/api/qa/issues'):
        return ok({'total':1,'items':[{'entry_id':'e1','status':'needs_review','file_path':'Data/A.json','source_text':'Open the gate','target_text':'打开大门','note':'gate → 城门'}]})
    if path.endswith('/api/entries/update') and method == 'POST':
        return ok({'updated':True})
    if path.endswith('/api/entries/e1/suggest'):
        return ok({'source':'ai','suggestion':'打开城门','provider':'mock','model':'mock','glossary_count':1})
    return ok({'items':[]})


def main():
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path=browser_executable(), args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1000,'height':700})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/**', route_api)
        html=load_html().replace('<head>','<head><base href="http://app.test/">',1)
        page.set_content(html)
        page.wait_for_timeout(900)
        assert page.locator('#entryId').inner_text()=='e1'
        assert page.get_by_role('button', name='QA this entry').count()==1
        page.get_by_role('button', name='QA this entry').click(); page.wait_for_timeout(120)
        assert 'QA' in page.locator('#v15QaInline').inner_text()
        assert page.get_by_role('button', name='Best TM').count()==1
        page.get_by_role('button', name='Best TM').click(); page.wait_for_timeout(30)
        assert page.locator('#targetText').input_value()=='打开城门'
        page.get_by_role('button', name='Restore saved').click(); page.wait_for_timeout(50)
        assert 'Restore' in page.locator('#assistBody').inner_text() or 'Glossary' in page.locator('#assistBody').inner_text()
        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
        assert not errors, errors
        browser.close()
    print('UI v18 runtime test: PASS')

if __name__=='__main__':
    main()
