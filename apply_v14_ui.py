from pathlib import Path
root=Path(__file__).parent
src=Path('/mnt/data/GameLocalizer_v13/ui_pages.py').read_text(encoding='utf-8')
# base css additions
src=src.replace('.tool-row{padding:7px 8px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);font-size:10px}', '.tool-row{padding:7px 8px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);font-size:10px}.context-card{padding:9px 10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);margin-top:7px}.context-label{font-size:9px;font-weight:850;color:var(--subtle);text-transform:uppercase;letter-spacing:.06em}.context-value{font-size:10px;line-height:1.5;margin-top:4px}.context-highlight{border-left:3px solid var(--accent);padding-left:8px}.assist-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:8px}.assist-stat{padding:7px 8px;border-radius:8px;background:var(--surface-2);border:1px solid var(--line)}.assist-stat strong{display:block;font-size:13px}.assist-stat span{font-size:9px;color:var(--muted)}')
# State
src=src.replace("let entries=[],selectedIndex=0,entryTotal=0,projectTotal=0,offset=0;const pageSize=50;let currentTask=null;let currentTaskName='';let reloadTimer=null;let editorDirty=false;let pendingSelect=-1;", "let entries=[],selectedIndex=0,entryTotal=0,projectTotal=0,offset=0;const pageSize=50;let currentTask=null;let currentTaskName='';let reloadTimer=null;let editorDirty=false;let pendingSelect=-1;let assistantContext=null;",1)
# clear editor
src=src.replace("document.getElementById('entryLock').textContent='—';", "assistantContext=null;document.getElementById('entryLock').textContent='—';",1)
# load assistant context function before updateLength
anchor='function updateLength(markDirty=true){'
func=r'''async function loadAssistantContext(){
  const e=entries[selectedIndex]; if(!e)return;
  const body=document.getElementById('assistBody'); if(!body)return;
  body.innerHTML='<div class="assist-card"><div class="assist-title">Context</div><div class="small">正在读取 Entry 上下文…</div></div>';
  try{
    const d=await api('/api/entries/'+encodeURIComponent(e.id)+'/assistant-context');
    assistantContext=d;
    renderContextPanel(d);
  }catch(err){
    assistantContext=null;
    body.innerHTML='<div class="assist-card"><div class="assist-title">Context</div><div class="empty-state"><strong>无法读取上下文</strong><div>'+safe(err.message||'')+'</div></div></div>';
  }
}
function renderContextPanel(d){
  const b=document.getElementById('assistBody'); if(!b)return;
  const e=d.entry||{}, qa=d.qa||{}, exact=(d.translation_memory?.exact||[])[0], glossary=d.glossary||[], neighbors=d.neighbors||[];
  let html='<div class="assist-card"><div class="assist-title">Entry Context</div>';
  html+='<div class="context-card context-highlight"><div class="context-label">Scene / Context</div><div class="context-value">'+safe(e.context||'未提供上下文')+'</div></div>';
  html+='<div class="context-card"><div class="context-label">File</div><div class="context-value">'+safe(e.file_path||'—')+'</div></div>';
  html+='<div class="context-card"><div class="context-label">Engine / Location</div><div class="context-value">'+safe(e.engine||'—')+' · '+safe(JSON.stringify(e.location||{}))+'</div></div>';
  if(e.note) html+='<div class="context-card"><div class="context-label">Note</div><div class="context-value">'+safe(e.note)+'</div></div>';
  html+='<div class="assist-summary"><div class="assist-stat"><strong>'+safe(glossary.length)+'</strong><span>Matched glossary terms</span></div><div class="assist-stat"><strong>'+safe((qa.issues||[]).length)+'</strong><span>Current QA issues</span></div></div></div>';
  if(glossary.length){html+='<div class="assist-card"><div class="assist-title">Glossary Applied</div><div class="tool-list">'+glossary.map(x=>'<div class="tool-row"><strong>'+safe(x.source_term)+' → '+safe(x.target_term)+'</strong><div class="small">'+safe(x.level)+'</div></div>').join('')+'</div></div>'}
  if(exact){html+='<div class="assist-card"><div class="assist-title">Translation Memory</div><div class="small">Exact source match</div><div class="suggestion">'+safe(exact.translated_text||'')+'</div><div class="small" style="margin-top:6px">'+safe(exact.human_reviewed?'Reviewed':'Machine')+(exact.provider?' · '+safe(exact.provider):'')+(exact.model?' · '+safe(exact.model):'')+'</div><div class="header-actions" style="margin-top:8px"><button class="btn sm primary" onclick="useMemoryExact()">Use exact match</button></div></div>'}
  else {html+='<div class="assist-card"><div class="assist-title">Translation Memory</div><div class="small">没有精确匹配。</div></div>'}
  if((qa.issues||[]).length){html+='<div class="assist-card"><div class="assist-title">QA</div>'+qa.issues.map(x=>'<div class="qa-item"><span class="qa-dot warn"></span><div><strong>'+safe(x.code)+'</strong><div class="small">'+safe(x.message)+'</div></div></div>').join('')+'</div>'}
  else {html+='<div class="assist-card"><div class="assist-title">QA</div><div class="qa-item"><span class="qa-dot ok"></span><div>当前 Entry 没有 QA 问题。</div></div></div>'}
  if(neighbors.length){html+='<div class="assist-card"><div class="assist-title">Same File</div><div class="tool-list">'+neighbors.map(x=>'<div class="tool-row"><div class="small">'+safe(x.relation)+' · '+safe(x.status)+'</div><div style="margin-top:4px">'+safe(x.source_text)+'</div><div class="small" style="margin-top:3px">'+safe(x.target_text||'—')+'</div></div>').join('')+'</div></div>'}
  b.innerHTML=html;
}
function useMemoryExact(){const exact=(assistantContext?.translation_memory?.exact||[])[0];if(!exact)return;const ta=document.getElementById('targetText');if(ta.disabled)return toast('这个条目已锁定，不能编辑。');ta.value=exact.translated_text||'';editorDirty=true;updateLength();document.getElementById('saveState').textContent='● Unsaved changes';toast('已填入 Translation Memory，请保存。')}
'''
assert anchor in src
src=src.replace(anchor,func+anchor,1)
# selectEntry exact replacement: only first function definition
needle_end="switchAssist('ai')}"
idx=src.find('function selectEntry(i){')
end=src.find(needle_end, idx)
assert idx>=0 and end>=0
old=src[idx:end+len(needle_end)]
old2=old.replace("switchAssist('ai')}", "assistantContext=null;switchAssist('context');loadAssistantContext() }")
src=src[:idx]+old2+src[end+len(needle_end):]
# context switch branch
oldbranch="}else if(tab==='context'){b.innerHTML='<div class=\"assist-card\"><div class=\"assist-title\">Entry Context</div><div class=\"small\">File</div><div style=\"font-size:10px;font-weight:850;margin-top:3px\">'+safe(document.getElementById('ctxFile').textContent)+'</div><div class=\"small\" style=\"margin-top:10px\">Note</div><div style=\"font-size:10px;font-weight:850;margin-top:3px\">'+safe(document.getElementById('ctxNote').textContent)+'</div></div>'}"
assert oldbranch in src
src=src.replace(oldbranch, "}else if(tab==='context'){if(assistantContext)renderContextPanel(assistantContext);else loadAssistantContext()}",1)
# initial render end. use last occurrence before settings
oldinit="renderEntryListV13();updateBulkBar();"
assert oldinit in src
src=src.replace(oldinit, oldinit+"if(entries[selectedIndex])loadAssistantContext();",1)
# Ensure we don't accidentally create broken duplicate JS; write
(root/'ui_pages.py').write_text(src,encoding='utf-8')
print('patched ui_pages:', 'loadAssistantContext' in src, 'context endpoint reference' in src)
