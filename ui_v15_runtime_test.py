from __future__ import annotations
from pathlib import Path
from playwright.sync_api import sync_playwright
from ui_test_support import browser_executable
ROOT=Path(__file__).resolve().parent

def load_html():
    import importlib.util
    spec=importlib.util.spec_from_file_location('ui_pages_runtime',ROOT/'ui_pages.py')
    mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)
    return mod.CONSOLE_PAGE

STUB=r'''(() => {
window.__saved=false;
window.fetch=async (input,init={})=>{
  const url=String(input),method=String(init.method||'GET').toUpperCase(),path=url.split('?')[0];
  const json=(d,status=200)=>Promise.resolve(new Response(JSON.stringify(d),{status,headers:{'Content-Type':'application/json'}}));
  if(path.endsWith('/api/meta'))return json({meta:{name:'Demo',source_language:'English',target_language:'zh-CN'},stats:{pending:0,machine_translated:1,needs_review:0,reviewed:0,locked:0,error:0}});
  if(path.endsWith('/api/entries'))return json({total:1,items:[{id:'e1',source_text:'Open the gate',target_text:'打开大门',context:'Castle ending',file_path:'Data/A.json',note:'Use glossary',status:'machine_translated',locked:false,ignored:false}]});
  if(path.includes('/api/entries/e1/assistant-context'))return json({entry:{id:'e1',source_text:'Open the gate',target_text:'打开大门',context:'Castle ending',file_path:'Data/A.json',engine:'rpgmaker',location:{line:1},note:'Use glossary',status:'machine_translated',locked:false,ignored:false},glossary:[{source_term:'gate',target_term:'城门',level:'required'}],translation_memory:{exact:[],matches:[{source_text:'Open the gates',translated_text:'打开城门',target_language:'zh-CN',human_reviewed:true}]},qa:{passed:false,issues:[{level:'warning',code:'glossary_missing',message:'gate → 城门'}]},neighbors:[]});
  if(path.endsWith('/api/tasks'))return json({items:[]});
  if(path.endsWith('/api/entries/suggest'))return json({suggestion:'打开城门',source:'ai',provider:'demo',model:'gemini',glossary_count:1});
  if(path.endsWith('/api/entries/update')&&method==='POST'){window.__saved=true;return json({updated:true});}
  return json({ok:true,items:[]});
};})();'''

def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=browser_executable(),args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1000,'height':700})
        msgs=[]
        page.on('console', lambda m: msgs.append('CONSOLE '+m.type+': '+m.text))
        page.on('pageerror', lambda e: msgs.append('PAGEERROR: '+str(e)))
        page.set_content('<script>'+STUB.replace('</script>','')+'</script>'+load_html())
        page.wait_for_timeout(800)
        assert page.locator('#v15QaInline').count()==1
        assert 'glossary_missing' in page.locator('#v15QaInline').inner_text()
        assert '城门' in page.locator('#assistBody').inner_text()
        page.get_by_role('button',name='Insert').first.click()
        page.wait_for_timeout(50)
        assert '城门' in page.locator('#targetText').input_value()
        page.get_by_role('button',name='Use').first.click()
        assert '打开城门' in page.locator('#targetText').input_value()
        page.locator('#targetText').fill('打开城门')
        page.keyboard.press('Control+S'); page.wait_for_timeout(250)
        assert page.evaluate('window.__saved === true')
        assert page.locator('#entryId').inner_text()=='e1'
        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
        browser.close()
    print('UI v15 runtime test: PASS')

if __name__=='__main__':main()
