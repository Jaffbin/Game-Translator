from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from ui_test_support import browser_executable

ROOT = Path(__file__).resolve().parent

FETCH_STUB = r"""
(() => {
  let statePolls = 0;
  let workflowStage = 'idle';
  window.__savedEntry = false;
  window.__deletedGlossary = false;
  window.__restoredArchived = false;
  window.__createdModPatch = false;
  window.__autoProvider = null;
  const original = window.fetch;
  window.fetch = async (input, init = {}) => {
    const url = String(input);
    const method = String(init.method || 'GET').toUpperCase();
    const path = url.split('?')[0];
    const payload = init.body ? JSON.parse(init.body) : null;
    const json = (data, status=200) => Promise.resolve(new Response(JSON.stringify(data), {
      status, headers: {'Content-Type':'application/json'}
    }));

    if (path.endsWith('/api/select_folder')) return json({path: 'D:\\Games\\DemoGame'});
    if (path.endsWith('/api/preferences')) return json({target_language:'zh-CN'});
    if (path.endsWith('/api/preflight')) return json({path:'D:\\Games\\DemoGame',engine:'rpgmaker_mv_mz',provider:'cloud',provider_ready:true,diagnostics:{can_scan:true,encoding:{counts:{'utf-8':1}},runtime:{markers:['RPG Maker MZ']},fonts:{total:0},issues:[]}});
    if (path.endsWith('/api/recovery')) return json({project:null,backup_count:0});
    if (path.endsWith('/api/prepare')) { workflowStage = 'preparing'; return json({ok:true}); }
    if (path.endsWith('/api/auto')) { workflowStage = 'translating'; statePolls = 0; return json({ok:true}); }
    if (path.endsWith('/api/translate')) { workflowStage = 'translating'; statePolls = 0; return json({ok:true}); }
    if (path.endsWith('/api/state')) {
      statePolls += 1;
      if (workflowStage === 'preparing') return json({state:'prepared',step:'等待确认',message:'扫描完成',logs:['scan ok'],entries:2,estimated_tokens:20,engine:'rpgmaker_mv_mz',provider:'cloud'});
      if (workflowStage === 'translating' && statePolls <= 1) return json({state:'running',busy:true,step:'翻译文本',message:'正在翻译…',logs:['started'],entries:2});
      if (workflowStage === 'translating') return json({state:'done',step:'完成',message:'翻译完成',logs:['done'],entries:2});
      return json({state:'idle',busy:false,logs:[]});
    }
    if (path.endsWith('/api/apply')) return json({ok:true});
    if (path.endsWith('/api/restore')) return json({ok:true});

    if (path.endsWith('/api/meta')) return json({meta:{name:'Demo Game',source_language:'English',target_language:'zh-CN'}, stats:{pending:0,machine_translated:1,needs_review:0,reviewed:0,locked:0,error:0}});
    if (path.endsWith('/api/entries')) return json({total:1,items:[{id:'e001',source_text:'Hello world',target_text:'你好世界',context:'Greeting',file_path:'dialog.txt',note:'',status:'machine_translated',locked:false,ignored:false}]});
    if (path.includes('/api/entries/e001/assistant-context')) return json({entry:{id:'e001',source_text:'Hello world',target_text:'你好世界',context:'Greeting',file_path:'dialog.txt',engine:'demo',location:{line:1},note:'',status:'machine_translated',locked:false,ignored:false},glossary:[],translation_memory:{exact:[],matches:[]},qa:{passed:true,issues:[]},neighbors:[]});
    if (path.endsWith('/api/tasks')) return json({items:[]});
    if (path.endsWith('/api/config/providers')) return json({items:[{id:'mock',type:'mock',ready:false},{id:'gemini',type:'openai_compatible',model:'flash-lite',ready:true},{id:'ollama',type:'openai_compatible',model:'qwen',ready:true}]});
    if (path.endsWith('/api/actions/auto')) { window.__autoProvider = payload?.provider; return json({task_id:'auto1'}); }
    if (path.endsWith('/api/tasks/auto1')) return json({name:'auto',status:'completed',logs:['Step 1/3: Scan game text','Step 2/3: Translate','Step 3/3: QA'],result:{translation:{translated:1},qa:{entries_with_errors:0}}});
    if (path.endsWith('/api/entries/update') && method === 'POST') { window.__savedEntry = true; return json({updated:true}); }
    if (path.endsWith('/api/glossary') && method === 'DELETE') { window.__deletedGlossary = true; return json({deleted:true}); }
    if (path.endsWith('/api/glossary')) return json({items:[{source_term:'Hero\'s "Blade"',target_term:'英雄之刃',level:'required',case_sensitive:false}]});
    if (path.endsWith('/api/patch/preview')) return json({total_entries:1,patchable_files:1,blocked_error_entries:0,qa_summary:{entries_with_errors:0},files:[]});
    if (path.endsWith('/api/modding/categories')) return json({items:[{id:'items',file:'Items.json',fields:[{key:'price',label:'Price',type:'integer',minimum:0,maximum:99999999}]}]});
    if (path.endsWith('/api/modding/catalog')) return json({category:'items',total:1,items:[{id:1,name:'Potion',values:{price:50}}]});
    if (path.endsWith('/api/modding/preview')) return json({records_changed:1,fields_changed:1,changes:[{category:'items',record_id:1,name:'Potion',fields:[{field:'price',label:'Price',before:50,after:99}]}]});
    if (path.endsWith('/api/modding/patch')) { window.__createdModPatch = true; return json({files:1,records_changed:1,fields_changed:1}); }
    if (path.endsWith('/api/patches')) return json({items:[]});
    if (path.endsWith('/api/backups')) return json({items:[]});

    if (path.endsWith('/api/projects/open')) return json({url:'#console'});
    if (path.endsWith('/api/storage')) return json({projects_root:'C:\\Users\\Tester\\AppData\\Local\\AutoGameLocalizer\\projects',migration:{projects_imported:2,errors:[]}});
    if (path.endsWith('/api/projects/restore') && method === 'POST') { window.__restoredArchived = true; return json({name:'Old Project'}); }
    if (path.endsWith('/api/projects/archived')) return json({items:[{archive_id:'Old_Project_20260929',name:'Old Project',engine:'renpy',target_language:'zh-CN'}]});
    if (path.endsWith('/api/projects')) return json({items:[{name:'Demo Project',engine:'rpgmaker_mv_mz',target_language:'zh-CN',updated_at:'2026-09-29T10:00:00',patches:0,backups:0}]});
    if (path.endsWith('/api/settings/config')) return json({translation:{target_language:'zh-CN'},providers:{demo:{type:'openai_compatible',model:'gemini',has_api_key:true}}});
    if (path.endsWith('/api/settings/env')) return json({items:[{key:'GEMINI_API_KEY',has_value:true}]});
    if (path.endsWith('/api/settings/general') || path.endsWith('/api/settings/provider') || path.endsWith('/api/settings/provider/test')) return json({ok:true,translated_text:'ok'});
    return original(input, init);
  };
})();
"""

def load_html(name: str) -> str:
    import importlib.util
    spec = importlib.util.spec_from_file_location('ui_pages_runtime', ROOT / 'ui_pages.py')
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return getattr(mod, name)


def assert_no_horizontal_overflow(page, label: str) -> None:
    overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
    assert not overflow, f'{label}: horizontal overflow'


def main() -> None:
    pages = ['PORTAL_PAGE','PLAYER_PAGE','WORKSPACE_PAGE','CONSOLE_PAGE','SETTINGS_PAGE']
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=browser_executable(), args=['--no-sandbox'])
        for width, height in [(1220,860),(1000,700)]:
            for name in pages:
                page = browser.new_page(viewport={'width':width,'height':height})
                page.set_content(load_html(name))
                page.add_init_script(FETCH_STUB)
                page.reload()
                page.wait_for_timeout(120)
                assert_no_horizontal_overflow(page, f'{name} {width}x{height}')
                page.close()

        page = browser.new_page(viewport={'width':1000,'height':700})
        page.set_content('<script>'+FETCH_STUB.replace('</script>','')+'</script>'+load_html('PLAYER_PAGE'))
        page.wait_for_timeout(50)
        page.get_by_role('button', name='选择文件夹').first.click()
        page.wait_for_timeout(50)
        assert page.get_by_role('button', name='一键扫描 → 翻译 → QA').is_enabled()
        page.get_by_role('button', name='一键扫描 → 翻译 → QA').click()
        page.wait_for_timeout(1200)
        assert page.locator('#globalStatus strong').inner_text() == 'Ready to apply'
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async text => {window.__copiedLog=text}}})")
        page.evaluate("""() => {
          showPlayerLog('翻译失败原因',['Original log line']);
          const log=document.getElementById('playerLogText'),range=document.createRange();
          range.setStart(log.firstChild,0);range.setEnd(log.firstChild,8);
          const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
          showPlayerLog('翻译失败原因',['Updated log line']);
        }""")
        assert page.locator('#playerLogText').inner_text() == 'Original log line'
        page.get_by_role('button', name='复制日志').click()
        assert 'Updated log line' in page.evaluate('window.__copiedLog')
        page.close()

        page = browser.new_page(viewport={'width':1000,'height':700})
        page.set_content('<script>'+FETCH_STUB.replace('</script>','')+'</script>'+load_html('CONSOLE_PAGE'))
        page.wait_for_timeout(700)
        assert page.locator('#consoleProvider').input_value() == 'gemini', page.locator('#consoleProvider').evaluate('(e) => ({value:e.value, options:[...e.options].map(x=>x.textContent), status:document.getElementById("consoleProviderStatus").textContent})')
        page.locator('#consoleProvider').select_option('ollama')
        assert page.get_by_role('button', name='Release', exact=True).is_visible()
        assert not page.get_by_role('button', name='Data Mod', exact=True).is_visible()
        page.get_by_role('button', name='? 按钮说明').click()
        assert '先备份' in page.locator('#consoleHelp').inner_text()
        page.get_by_role('button', name='Workflow', exact=True).click()
        page.get_by_role('button', name='自动执行 Scan → Translate → QA').click()
        page.wait_for_timeout(150)
        assert page.evaluate('window.__autoProvider') == 'ollama'
        assert 'Step 3/3: QA' in page.locator('#consoleLog').inner_text()
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async text => {window.__copiedLog=text}}})")
        page.get_by_role('button', name='复制日志').click()
        assert 'Step 3/3: QA' in page.evaluate('window.__copiedLog')
        page.evaluate("""() => {
          const log=document.getElementById('consoleLog'),range=document.createRange();
          range.setStart(log.firstChild,0);range.setEnd(log.firstChild,4);
          const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
          updateSelectableLog('consoleLog','A newer log line');
        }""")
        assert page.locator('#consoleLog').inner_text().startswith('Step 1/3')
        assert page.evaluate("window.getSelection().toString().length") == 4
        page.evaluate('window.getSelection().removeAllRanges()')
        page.wait_for_timeout(50)
        assert page.locator('#consoleLog').inner_text() == 'A newer log line'
        page.evaluate('setBusy(true)')
        assert page.locator('#busyOverlay').evaluate('(e) => getComputedStyle(e).pointerEvents') == 'none'
        page.locator('#consoleLog').scroll_into_view_if_needed()
        log_rect = page.locator('#consoleLog').bounding_box()
        assert log_rect is not None
        page.mouse.move(log_rect['x'] + 16, log_rect['y'] + 21)
        page.mouse.down()
        page.mouse.move(log_rect['x'] + 130, log_rect['y'] + 21, steps=8)
        page.mouse.up()
        assert page.evaluate('window.getSelection().toString().length') > 0
        page.evaluate('setBusy(false)')
        page.get_by_role('button', name='Entries', exact=True).click()
        assert 'Entry Context' in page.locator('#assistBody').inner_text()
        page.locator('#targetText').fill('你好世界（已审校）')
        page.get_by_role('button', name='Save', exact=True).click()
        page.wait_for_timeout(100)
        assert page.locator('#saveState').inner_text() == '✓ Saved'
        assert page.evaluate('window.__savedEntry === true')
        page.evaluate("switchAssist('glossary')")
        page.get_by_role('button', name='Delete', exact=True).wait_for(state='visible')
        page.get_by_role('button', name='Delete', exact=True).click()
        page.get_by_role('button', name='删除', exact=True).click()
        page.wait_for_timeout(100)
        assert page.evaluate('window.__deletedGlossary === true')
        page.get_by_role('button', name='Release', exact=True).click()
        page.get_by_role('button', name='Data Mod', exact=True).click()
        page.get_by_role('button', name='#1 · Potion', exact=True).wait_for(state='visible')
        page.get_by_role('button', name='#1 · Potion', exact=True).click()
        page.locator('[data-mod-field="price"]').fill('99')
        page.get_by_role('button', name='Create Patch', exact=True).click()
        page.wait_for_timeout(100)
        assert page.evaluate('window.__createdModPatch === true')
        page.close()

        page = browser.new_page(viewport={'width':1000,'height':700})
        page.set_content('<script>'+FETCH_STUB.replace('</script>','')+'</script>'+load_html('SETTINGS_PAGE'))
        page.wait_for_timeout(1000)
        page.get_by_role('button', name='Providers', exact=True).click()
        page.get_by_role('button', name='Edit', exact=True).wait_for(state='visible')
        page.get_by_role('button', name='Edit', exact=True).click()
        assert page.locator('#p_id').input_value() == 'demo'
        page.close()

        page = browser.new_page(viewport={'width':1000,'height':700})
        page.set_content('<script>'+FETCH_STUB.replace('</script>','')+'</script>'+load_html('WORKSPACE_PAGE'))
        page.get_by_role('button', name='浏览…', exact=True).click()
        assert page.locator('#newGamePath').input_value() == r'D:\Games\DemoGame'
        assert page.locator('#newName').input_value() == 'DemoGame'
        page.locator('#archivedProjects summary').click()
        page.get_by_role('button', name='恢复', exact=True).wait_for(state='visible')
        page.get_by_role('button', name='恢复', exact=True).click()
        page.locator('#confirmBackdrop').get_by_role('button', name='恢复', exact=True).click()
        page.wait_for_timeout(100)
        assert page.evaluate('window.__restoredArchived === true')
        page.get_by_role('button', name='Open Console', exact=True).wait_for(state='visible')
        page.get_by_role('button', name='Open Console', exact=True).click()
        page.wait_for_timeout(50)
        assert page.url.endswith('#console')
        page.close()

        browser.close()
    print('UI runtime test: PASS')
    print('- 1000x700 and 1220x860 horizontal overflow: PASS')
    print('- Player select -> start -> completed: PASS')
    print('- Console entry edit -> save: PASS')
    print('- Dynamic project, folder picker, data mod, glossary and provider actions: PASS')


if __name__ == '__main__':
    main()
