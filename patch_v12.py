from pathlib import Path
import re

root = Path('/mnt/data/GameLocalizer_v12')

# 1) Translation memory: add exact lookup + browse/search.
cache = root/'agl/cache.py'
s = cache.read_text()
needle = '''    def put(\n'''
insert = '''    def get_exact_any(\n        self,\n        source_text: str,\n        target_language: str,\n        prefer_human_reviewed: bool = True,\n    ) -> Optional[dict]:\n        """Return the most recently updated exact source/target-language memory item."""\n        order = "human_reviewed DESC, updated_at DESC" if prefer_human_reviewed else "updated_at DESC"\n        cursor = self.conn.execute(\n            f"""\n            SELECT source_text, target_language, translated_text, provider, model,\n                   human_reviewed, updated_at\n            FROM translation_cache\n            WHERE source_text = ? AND target_language = ?\n            ORDER BY {order}\n            LIMIT 1\n            """,\n            ((source_text or "").strip(), target_language or ""),\n        )\n        row = cursor.fetchone()\n        return dict(row) if row else None\n\n    def search(\n        self,\n        query: str = "",\n        target_language: str = "",\n        limit: int = 50,\n    ) -> list[dict]:\n        """Search recent translation memory entries without exposing cache hashes."""\n        query = (query or "").strip()\n        limit = max(1, min(int(limit), 200))\n        clauses = []\n        params = []\n        if query:\n            like = f"%{query}%"\n            clauses.append("(source_text LIKE ? OR translated_text LIKE ?)")\n            params.extend([like, like])\n        if target_language:\n            clauses.append("target_language = ?")\n            params.append(target_language)\n        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""\n        cursor = self.conn.execute(\n            f"""\n            SELECT source_text, target_language, translated_text, provider, model,\n                   human_reviewed, updated_at\n            FROM translation_cache\n            {where}\n            ORDER BY human_reviewed DESC, updated_at DESC\n            LIMIT ?\n            """,\n            (*params, limit),\n        )\n        return [dict(row) for row in cursor.fetchall()]\n\n'''
if needle not in s:
    raise SystemExit('cache insertion point not found')
s = s.replace(needle, insert + needle, 1)
cache.write_text(s)

# 2) Operations: glossary CRUD + memory search + one-entry suggestion + pass glossary to batch translation.
ops = root/'agl/operations.py'
s = ops.read_text()
s = s.replace('import datetime\nimport os\n', 'import csv\nimport datetime\nimport os\n', 1)
old = '''        cache_namespace = f"{provider.id}:{provider.model}"\n        target_language = (\n'''
new = '''        cache_namespace = f"{provider.id}:{provider.model}"\n        glossary_path = Path(store.project_dir) / "glossary.csv"\n        glossary_entries = load_glossary(glossary_path)\n        glossary = {item.source_term: item.target_term for item in glossary_entries if item.source_term}\n        target_language = (\n'''
if old not in s:
    raise SystemExit('translate glossary insertion not found')
s = s.replace(old, new, 1)
s = s.replace('''                        context=entry.context,\n                        use_cache=not no_cache,\n''','''                        context=entry.context,\n                        glossary=glossary,\n                        use_cache=not no_cache,\n''', 1)
marker = '''def get_provider_configs() -> List[Dict[str, Any]]:\n'''
block = r'''

def list_project_glossary(
    project_dir: Path | str,
    query: str = "",
) -> List[Dict[str, Any]]:
    """List project glossary entries from glossary.csv without exposing filesystem paths."""
    path = Path(project_dir) / "glossary.csv"
    entries = load_glossary(path)
    q = (query or "").strip().lower()
    items = []
    for entry in entries:
        if q and q not in entry.source_term.lower() and q not in entry.target_term.lower():
            continue
        items.append({
            "source_term": entry.source_term,
            "target_term": entry.target_term,
            "level": entry.level,
            "case_sensitive": entry.case_sensitive,
        })
    return items


def upsert_project_glossary(
    project_dir: Path | str,
    source_term: str,
    target_term: str,
    level: str = "required",
    case_sensitive: bool = False,
) -> Dict[str, Any]:
    """Create or update one glossary term; the project stores a portable CSV."""
    source_term = (source_term or "").strip()
    target_term = (target_term or "").strip()
    level = (level or "required").strip().lower()
    if not source_term:
        raise ValueError("Source term is required.")
    if not target_term:
        raise ValueError("Target term is required.")
    if level not in {"required", "preferred"}:
        raise ValueError("Level must be required or preferred.")

    path = Path(project_dir) / "glossary.csv"
    current = list_project_glossary(project_dir)
    updated = False
    for item in current:
        if item["source_term"].casefold() == source_term.casefold():
            item.update({
                "source_term": source_term,
                "target_term": target_term,
                "level": level,
                "case_sensitive": bool(case_sensitive),
            })
            updated = True
            break
    if not updated:
        current.append({
            "source_term": source_term,
            "target_term": target_term,
            "level": level,
            "case_sensitive": bool(case_sensitive),
        })

    current.sort(key=lambda x: x["source_term"].casefold())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source_term", "target_term", "level", "case_sensitive"],
        )
        writer.writeheader()
        writer.writerows(current)
    return {
        "updated": updated,
        "entry": {
            "source_term": source_term,
            "target_term": target_term,
            "level": level,
            "case_sensitive": bool(case_sensitive),
        },
        "count": len(current),
    }


def delete_project_glossary(
    project_dir: Path | str,
    source_term: str,
) -> bool:
    """Delete a glossary term by source term. Returns whether a row was removed."""
    source_term = (source_term or "").strip()
    if not source_term:
        raise ValueError("Source term is required.")
    path = Path(project_dir) / "glossary.csv"
    current = list_project_glossary(project_dir)
    filtered = [x for x in current if x["source_term"].casefold() != source_term.casefold()]
    removed = len(filtered) != len(current)
    if not removed:
        return False
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source_term", "target_term", "level", "case_sensitive"],
        )
        writer.writeheader()
        writer.writerows(filtered)
    return True


def search_translation_memory(
    query: str = "",
    target_language: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    config = load_config()
    with TranslationMemory(config.cache_db) as cache:
        return cache.search(query=query, target_language=target_language, limit=limit)


def suggest_entry_translation(
    project_dir: Path | str,
    entry_id: str,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
    force_ai: bool = False,
) -> Dict[str, Any]:
    """Return an exact TM hit when possible, otherwise generate a one-entry AI suggestion."""
    config = load_config()
    with ProjectStore(project_dir) as store:
        entry = store.get_entry(entry_id)
        if entry is None:
            raise ValueError("Entry not found.")
        target_language = store.meta.get("target_language") or config.target_language
        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary = {
            item.source_term: item.target_term
            for item in load_glossary(glossary_path)
            if item.source_term and item.target_term
        }
        source = entry.source_text or ""
        if not force_ai:
            with TranslationMemory(config.cache_db) as cache:
                memory = cache.get_exact_any(source, target_language)
            if memory:
                return {
                    "source": "translation_memory",
                    "suggestion": memory["translated_text"],
                    "provider": memory.get("provider", ""),
                    "model": memory.get("model", ""),
                    "human_reviewed": bool(memory.get("human_reviewed")),
                    "glossary_count": len(glossary),
                }

        provider_id = provider_id or config.first_available_provider()
        provider_config = config.get_provider(provider_id)
        if provider_config is None:
            raise ValueError(f"Provider not found: {provider_id}")
        provider = create_provider(provider_config, model_override=model or None)
        suggestion = translate_one(
            provider=provider,
            cache=TranslationMemory(config.cache_db),
            source_text=source,
            target_language=target_language,
            context=entry.context,
            glossary=glossary,
            use_cache=False,
        )
        return {
            "source": "ai",
            "suggestion": suggestion,
            "provider": provider.id,
            "model": provider.model,
            "human_reviewed": False,
            "glossary_count": len(glossary),
        }

'''
# Fix resource handling in suggestion: replace problematic direct TranslationMemory construction with context manager and explicit placeholder-safe provider path below.
block = block.replace('''        suggestion = translate_one(\n            provider=provider,\n            cache=TranslationMemory(config.cache_db),\n            source_text=source,\n            target_language=target_language,\n            context=entry.context,\n            glossary=glossary,\n            use_cache=False,\n        )\n''','''        with TranslationMemory(config.cache_db) as cache:\n            suggestion = translate_one(\n                provider=provider,\n                cache=cache,\n                source_text=source,\n                target_language=target_language,\n                context=entry.context,\n                glossary=glossary,\n                use_cache=False,\n            )\n''')
if marker not in s:
    raise SystemExit('operations marker not found')
s = s.replace(marker, block + '\n' + marker, 1)
ops.write_text(s)

# 3) phase75_web: schemas + endpoints.
web = root/'phase75_web.py'
s = web.read_text()
needle = '''class QARequest(BaseModel):\n'''
insert = '''class GlossaryUpdate(BaseModel):\n    source_term: str\n    target_term: str\n    level: str = "required"\n    case_sensitive: bool = False\n\nclass SuggestRequest(BaseModel):\n    entry_id: str\n    provider: Optional[str] = None\n    model: Optional[str] = None\n    force_ai: bool = False\n\n'''
if needle not in s:
    raise SystemExit('schema insertion point not found')
s = s.replace(needle, insert + needle, 1)
marker = '''    # ------------------------------------------------------------------\n    # Lists\n'''
block = '''    # ------------------------------------------------------------------\n    # Glossary / translation memory / single-entry AI assistant\n    # ------------------------------------------------------------------\n    @app.get("/api/glossary")\n    def api_glossary(q: str = Query(default="")):\n        require_project()\n        return {"items": operations.list_project_glossary(project_dir, q)}\n\n    @app.post("/api/glossary")\n    def api_glossary_update(body: GlossaryUpdate):\n        require_project()\n        try:\n            return operations.upsert_project_glossary(\n                project_dir,\n                source_term=body.source_term,\n                target_term=body.target_term,\n                level=body.level,\n                case_sensitive=body.case_sensitive,\n            )\n        except ValueError as exc:\n            raise HTTPException(status_code=400, detail=str(exc))\n\n    @app.delete("/api/glossary")\n    def api_glossary_delete(source_term: str = Query(...)):\n        require_project()\n        try:\n            removed = operations.delete_project_glossary(project_dir, source_term)\n        except ValueError as exc:\n            raise HTTPException(status_code=400, detail=str(exc))\n        if not removed:\n            raise HTTPException(status_code=404, detail="Glossary term not found.")\n        return {"deleted": True}\n\n    @app.get("/api/translation-memory")\n    def api_translation_memory(\n        q: str = Query(default=""),\n        language: str = Query(default=""),\n        limit: int = Query(default=50, ge=1, le=200),\n    ):\n        require_project()\n        return {"items": operations.search_translation_memory(q, language, limit)}\n\n    @app.post("/api/entries/suggest")\n    def api_entries_suggest(body: SuggestRequest):\n        require_project()\n        try:\n            return operations.suggest_entry_translation(\n                project_dir,\n                entry_id=body.entry_id,\n                provider_id=body.provider,\n                model=body.model,\n                force_ai=body.force_ai,\n            )\n        except ValueError as exc:\n            raise HTTPException(status_code=400, detail=str(exc))\n        except Exception as exc:\n            raise HTTPException(status_code=502, detail="无法生成建议，请检查 Provider 配置。") from exc\n\n'''
if marker not in s:
    raise SystemExit('web list marker not found')
s = s.replace(marker, block + marker, 1)
web.write_text(s)

# 4) ui_pages: add Memory tab, functional glossary/memory/AI panels.
uip = root/'ui_pages.py'
s = uip.read_text()
s = s.replace('<button class="right-tab" data-tab="glossary" onclick="switchAssist(\'glossary\')">Glossary</button><button class="right-tab" data-tab="qa"', '<button class="right-tab" data-tab="glossary" onclick="switchAssist(\'glossary\')">Glossary</button><button class="right-tab" data-tab="memory" onclick="switchAssist(\'memory\')">Memory</button><button class="right-tab" data-tab="qa"', 1)
# Replace regenerate through applyHashTool region using a function-level regex.
pattern = re.compile(r"function regenerate\(\)\{.*?\nfunction setBusy\(busy\)", re.S)
replacement = r'''async function regenerate(force=true){if(!selectedEntry)return;try{document.getElementById('assistBody').innerHTML='<div class="assist-card"><div class="assist-title">AI Suggestion</div><div class="small">正在生成…</div></div>';const d=await api('/api/entries/suggest','POST',{entry_id:selectedEntry.id,force_ai:!!force});renderAiSuggestion(d)}catch(e){toast(e.message||'无法生成 AI 建议。');switchAssist('ai')}}
async function renderAiSuggestion(d){const b=document.getElementById('assistBody');b.innerHTML='<div class="assist-card"><div class="assist-title">AI Suggestion</div><div class="small">来源：'+safe(d.source==='translation_memory'?'Translation Memory':'AI')+(d.human_reviewed?' · 已人工确认':'')+'</div><div class="suggestion" id="aiSuggestionText" style="white-space:pre-wrap">'+safe(d.suggestion||'')+'</div><div class="small" style="margin-top:8px">'+safe((d.provider||'')+(d.model?' · '+d.model:''))+' · Glossary '+safe(d.glossary_count??0)+'</div><div class="header-actions" style="margin-top:9px"><button class="btn sm primary" onclick="useSuggestion()">Use suggestion</button><button class="btn sm" onclick="regenerate(true)">Regenerate</button></div></div><div class="assist-card"><div class="assist-title">Safety</div><div class="qa-item"><span class="qa-dot ok"></span><div>API Key 不在页面显示</div></div><div class="qa-item"><span class="qa-dot ok"></span><div>锁定条目不会被覆盖</div></div></div>')}
function useSuggestion(){const text=document.getElementById('aiSuggestionText')?.textContent||'';if(!text)return;document.getElementById('targetText').value=text;editorDirty=true;updateLength();document.getElementById('saveState').textContent='● Unsaved changes';toast('已填入编辑器，请保存。')}
async function loadGlossaryPanel(){const q=prompt('搜索词条（留空查看全部）','');if(q===null)return;try{const d=await api('/api/glossary?q='+encodeURIComponent(q));renderGlossary(d.items||[])}catch(e){toast('无法读取 Glossary。')}}
function renderGlossary(items){const b=document.getElementById('assistBody');b.innerHTML='<div class="tool-pane"><div class="toolbar"><div class="assist-title">Glossary</div><div class="top-spacer"></div><button class="btn sm primary" onclick="addGlossaryTerm()">+ Add</button></div><div class="small" style="margin-top:7px">这些词条会参与后续 AI 翻译与 QA。</div><div class="tool-list" style="margin-top:9px">'+(items.length?items.map(x=>'<div class="tool-row"><div><strong>'+safe(x.source_term)+' → '+safe(x.target_term)+'</strong><div class="small">'+safe(x.level)+' · '+(x.case_sensitive?'Case sensitive':'Case insensitive')+'</div></div><button class="btn sm danger" onclick="deleteGlossaryTerm('+JSON.stringify(x.source_term)+')">Delete</button></div>').join(''):'<div class="empty-state"><strong>暂无 Glossary</strong><div>添加常用术语后，翻译和 QA 会保持一致。</div></div>')+'</div></div>'}
async function addGlossaryTerm(){const source=prompt('原文术语','');if(!source)return;const target=prompt('目标术语','');if(!target)return;const level=prompt('级别：required / preferred','required')||'required';try{await api('/api/glossary','POST',{source_term:source,target_term:target,level,case_sensitive:false});toast('Glossary 已保存');loadGlossaryPanel()}catch(e){toast(e.message||'Glossary 保存失败。')}}
function deleteGlossaryTerm(source){openConfirm('删除 Glossary','确认删除「'+source+'」吗？','删除',async()=>{try{await api('/api/glossary?source_term='+encodeURIComponent(source),'DELETE');toast('Glossary 已删除');loadGlossaryPanel()}catch(e){toast('Glossary 删除失败。')}})}
async function loadMemoryPanel(){try{const d=await api('/api/translation-memory?language='+encodeURIComponent((document.getElementById('sideLang').textContent||'').split('→').pop().trim()));renderMemory(d.items||[])}catch(e){toast('无法读取 Translation Memory。')}}
function renderMemory(items){const b=document.getElementById('assistBody');b.innerHTML='<div class="tool-pane"><div class="toolbar"><div class="assist-title">Translation Memory</div><div class="top-spacer"></div><button class="btn sm" onclick="memorySearch()">Search</button></div><div class="small" style="margin-top:7px">优先显示已人工确认的历史翻译。</div><div class="tool-list" style="margin-top:9px">'+(items.length?items.slice(0,30).map(x=>'<div class="tool-row"><div><strong>'+safe(x.source_text)+'</strong><div style="margin-top:4px">'+safe(x.translated_text)+'</div><div class="small" style="margin-top:4px">'+safe(x.target_language)+' · '+(x.human_reviewed?'Reviewed':'Machine')+'</div></div></div>').join(''):'<div class="empty-state"><strong>暂无 Translation Memory</strong><div>完成翻译后，历史结果会自动出现在这里。</div></div>')+'</div></div>'}
async function memorySearch(){const q=prompt('搜索 source / translation','');if(q===null)return;try{const d=await api('/api/translation-memory?q='+encodeURIComponent(q)+'&language='+encodeURIComponent((document.getElementById('sideLang').textContent||'').split('→').pop().trim()));renderMemory(d.items||[])}catch(e){toast('搜索 Translation Memory 失败。')}}
function setBusy(busy)'''
if not pattern.search(s):
    raise SystemExit('ui regenerate block not found')
s = pattern.sub(replacement, s, count=1)
# Replace switchAssist + showTool area.
pattern2 = re.compile(r"function switchAssist\(tab\)\{.*?\nfunction setBusy\(busy\)", re.S)
replacement2 = r'''function switchAssist(tab){document.querySelectorAll('.right-tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));if(tab==='ai'){if(selectedEntry){regenerate(false)}else{document.getElementById('assistBody').innerHTML='<div class="empty-state"><strong>选择一个 Entry</strong><div>选择条目后，这里会显示 AI Suggestion。</div></div>'}}else if(tab==='context'){const b=document.getElementById('assistBody');b.innerHTML='<div class="assist-card"><div class="assist-title">Entry Context</div><div class="small">File</div><div style="font-size:10px;font-weight:850;margin-top:3px">'+safe(document.getElementById('ctxFile').textContent)+'</div><div class="small" style="margin-top:10px">Note</div><div style="font-size:10px;font-weight:850;margin-top:3px">'+safe(document.getElementById('ctxNote').textContent)+'</div></div>'}else if(tab==='glossary'){loadGlossaryPanel()}else if(tab==='memory'){loadMemoryPanel()}else{document.getElementById('assistBody').innerHTML='<div class="assist-card"><div class="assist-title">QA</div><div class="qa-item"><span class="qa-dot ok"></span><div>可从顶部执行 QA 检查</div></div><div class="qa-item"><span class="qa-dot warn"></span><div>任务完成后会在 Activity 与日志中显示结果。</div></div></div>'}}
async function showTool(name){if(name==='qa'){switchAssist('qa');return}if(name==='patch'){await previewPatch();return}if(name==='history'){try{const d=await api('/api/tasks');const items=(d.items||[]).slice(0,12);document.querySelectorAll('.right-tab').forEach(x=>x.classList.remove('active'));document.getElementById('assistBody').innerHTML='<div class="tool-pane"><div class="assist-title">Task History</div><div class="tool-list" style="margin-top:8px">'+(items.length?items.map(x=>'<div class="tool-row"><strong>'+safe(actionLabel(x.name))+'</strong><div class="small">'+safe(x.status)+' · '+safe(x.created_at||'')+'</div></div>').join(''):'<div class="small">No task history.</div>')+'</div></div>'}catch(e){toast('无法读取任务历史。')}return}if(name==='glossary'||name==='memory'){switchAssist(name);return}if(name==='entries'){switchAssist('context')}}
function setBusy(busy)'''
if not pattern2.search(s):
    raise SystemExit('ui switch block not found')
s = pattern2.sub(replacement2, s, count=1)
uip.write_text(s)

# 5) Add a small API note doc.
(root/'UI_INTEGRATION_V12.md').write_text('''# UI Integration V12\n\nV12 adds:\n- Project glossary CRUD via /api/glossary\n- Translation Memory browse/search via /api/translation-memory\n- Single-entry suggestion via /api/entries/suggest\n- Batch translation now passes project glossary into provider prompts\n\nOld phase files remain in place.\n''')

print('patched v12')
