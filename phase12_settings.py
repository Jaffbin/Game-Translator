from __future__ import annotations

import argparse
from typing import Any, Dict

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agl import config_manager


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Settings</title>
<style>
:root {
  --bg: #f8fafc; --surface: #ffffff; --primary: #2563eb; --primary-hover: #1d4ed8;
  --text-main: #0f172a; --text-muted: #64748b; --border: #e2e8f0;
  --success: #10b981; --warning: #f59e0b; --danger: #ef4444;
  --radius: 12px; --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text-main); display: flex; min-height: 100vh; }

/* Sidebar */
.sidebar { width: 220px; background: #0f172a; color: #cbd5e1; padding: 20px 12px; flex-shrink: 0; position: sticky; top: 0; height: 100vh; overflow-y: auto; display: flex; flex-direction: column; }
.sidebar .logo { color: #fff; font-weight: 700; font-size: 15px; padding: 0 10px 18px 10px; border-bottom: 1px solid #1e293b; margin-bottom: 12px; }
.sidebar a { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-radius: 8px; color: #cbd5e1; text-decoration: none; font-size: 14px; margin-bottom: 4px; }
.sidebar a.active, .sidebar a:hover { background: #1e293b; color: #fff; }
.sidebar a.home { margin-top: auto; color: #94a3b8; border-top: 1px solid #1e293b; padding-top: 16px; border-radius: 0; }

/* Main */
.main { flex: 1; padding: 24px 32px; overflow-x: hidden; min-width: 0; max-width: 1000px; }
.head { margin-bottom: 8px; }
.head h1 { margin: 0; font-size: 24px; }
.sub { color: var(--text-muted); font-size: 14px; margin: 0 0 24px 0; }

/* Tabs */
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
.tab { padding: 10px 16px; background: none; border: none; border-bottom: 2px solid transparent; font-size: 14px; font-weight: 500; color: var(--text-muted); cursor: pointer; border-radius: 0; }
.tab:hover { color: var(--text-main); background: none; }
.tab.active { color: var(--primary); border-bottom-color: var(--primary); }
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Cards & Forms */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; box-shadow: var(--shadow); }
.card h3 { margin: 0 0 16px 0; font-size: 16px; }
.form-grid { display: grid; grid-template-columns: 160px 1fr; gap: 12px 16px; align-items: center; margin-bottom: 16px; }
.form-grid label { font-size: 13px; font-weight: 500; color: var(--text-muted); }
.form-grid input, .form-grid select { width: 100%; max-width: 400px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; outline: none; }
.form-grid input:focus, .form-grid select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
.hint { font-size: 12px; color: var(--text-muted); margin: -8px 0 16px 0; }

/* Buttons */
button { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border); background: #fff; font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.15s; }
button:hover { background: #f1f5f9; }
.btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.btn-primary:hover { background: var(--primary-hover); }
.btn-secondary { background: #f1f5f9; color: var(--text-main); }
.btn-danger { color: var(--danger); border-color: #fecaca; }
.btn-danger:hover { background: #fef2f2; }
button.small { padding: 4px 10px; font-size: 12px; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; padding: 10px 12px; border-bottom: 1px solid var(--border); background: #f8fafc; }
td { padding: 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.muted { color: var(--text-muted); font-size: 13px; }

/* Badges */
.badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 600; }
.badge.green { background: #dcfce7; color: #166534; }
.badge.gray { background: #e2e8f0; color: #475569; }
</style>
</head>
<body>
  <div class="sidebar">
    <div class="logo">⌘ Developer Workspace</div>
    <a href="__PORTAL_URL__">◫ 项目列表</a>
    <a href="__PORTAL_URL__">▤ Project Console</a>
    <a href="#" class="active">⚙ Settings</a>
    <a href="__PORTAL_URL__" class="home">← 返回首页</a>
  </div>

  <div class="main">
    <div class="head"><h1>⚙ Settings</h1></div>
    <p class="sub">管理应用基础选项、Provider 连接与 API Key。敏感密钥只显示是否已配置，不显示明文。</p>

    <div class="tabs">
      <button class="tab active" onclick="showTab('general')">General</button>
      <button class="tab" onclick="showTab('providers')">Providers</button>
      <button class="tab" onclick="showTab('keys')">API Keys</button>
    </div>

    <!-- General Tab -->
    <div id="tab-general" class="tab-content active">
      <div class="card">
        <h3>General Options</h3>
        <div class="form-grid">
          <label>Interface Language</label>
          <select disabled><option>简体中文</option></select>
          
          <label>Default Target Language</label>
          <select id="gen_target_lang">
            <option value="zh-CN">简体中文</option>
            <option value="zh-TW">繁体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>

          <label>Backup Policy</label>
          <select>
            <option>Before Install / Patch</option>
            <option>Before every translation run</option>
            <option>Manual only</option>
          </select>
        </div>
        <p class="hint">建议保留自动备份。Rollback 会依赖最近一次可用备份。</p>
        <button class="btn-primary" onclick="saveGeneral()">Save Changes</button>
      </div>
    </div>

    <!-- Providers Tab -->
    <div id="tab-providers" class="tab-content">
      <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
          <h3 style="margin:0;">Providers</h3>
          <button class="btn-primary" onclick="newProvider()">+ Add Provider</button>
        </div>
        <table>
          <thead><tr><th>ID</th><th>Type</th><th>Model</th><th>API Key Env</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody id="providers_body"></tbody>
        </table>
      </div>

      <div class="card" id="editor_card" style="display:none;">
        <h3>Provider Editor</h3>
        <div class="form-grid">
          <label>Provider ID</label>
          <input type="text" id="p_id" placeholder="e.g. openrouter">
          
          <label>Type</label>
          <select id="p_type">
            <option value="openai_compatible">openai_compatible</option>
            <option value="mock">mock</option>
          </select>

          <label>Base URL</label>
          <input type="text" id="p_url" placeholder="https://api...">

          <label>Model</label>
          <input type="text" id="p_model" placeholder="model name">

          <label>API Key Env Var</label>
          <input type="text" id="p_env" placeholder="OPENROUTER_API_KEY">

          <label>Timeout (s)</label>
          <input type="number" id="p_timeout" value="60">

          <label>Max Retries</label>
          <input type="number" id="p_retries" value="3">

          <label>Temperature</label>
          <input type="number" step="0.1" id="p_temp" value="0.2">
        </div>
        <div style="display:flex; gap:8px; margin-top:16px; align-items:center;">
          <button class="btn-primary" onclick="saveProvider()">Save Provider</button>
          <button class="btn-secondary" onclick="testProvider()">Test Connection</button>
          <button class="btn-danger" onclick="deleteProvider()">Delete</button>
          <span id="editor_msg" class="muted" style="margin-left:auto;"></span>
        </div>
      </div>
    </div>

    <!-- API Keys Tab -->
    <div id="tab-keys" class="tab-content">
      <div class="card">
        <h3>API Keys Status</h3>
        <p class="hint">Secrets hidden. API Key 输入框始终使用 password 类型；界面只显示“已配置 / 未配置”，绝不展示明文。</p>
        <table>
          <thead><tr><th>Environment Variable</th><th>Status</th></tr></thead>
          <tbody id="keys_body"></tbody>
        </table>
        
        <h3 style="margin-top:24px;">Update Key</h3>
        <div class="form-grid">
          <label>Variable Name</label>
          <input type="text" id="k_name" placeholder="NVIDIA_API_KEY">
          
          <label>Secret Value</label>
          <input type="password" id="k_val" placeholder="••••••••••••">
        </div>
        <button class="btn-primary" style="margin-top:16px;" onclick="saveKey()">Save Key</button>
      </div>
    </div>
  </div>

<script>
  async function api(url, method, body) {
    const opts = { method: method || "GET", headers: {} };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  function showTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.tab[onclick="showTab('${name}')"]`).classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
  }

  async function loadAll() {
    await loadConfig();
    await loadKeys();
  }

  async function loadConfig() {
    try {
      const data = await api("/api/settings/config");
      document.getElementById("gen_target_lang").value = data.translation?.target_language || "zh-CN";
      
      const tbody = document.getElementById("providers_body");
      tbody.innerHTML = "";
      for (const [id, p] of Object.entries(data.providers || {})) {
        const tr = document.createElement("tr");
        const status = p.has_api_key ? '<span class="badge green">已配置</span>' : '<span class="badge gray">未配置</span>';
        tr.innerHTML = `
          <td><b>${id}</b></td>
          <td>${p.type}</td>
          <td class="muted">${p.model || '-'}</td>
          <td class="muted">${p.api_key_env || '-'}</td>
          <td>${status}</td>
          <td><button class="small" onclick='editProvider(${JSON.stringify(id)}, ${JSON.stringify(p)})'>Edit</button></td>
        `;
        tbody.appendChild(tr);
      }
    } catch(e) { console.error(e); }
  }

  async function loadKeys() {
    try {
      const data = await api("/api/settings/env");
      const tbody = document.getElementById("keys_body");
      tbody.innerHTML = "";
      (data.items || []).forEach(item => {
        const tr = document.createElement("tr");
        const status = item.has_value ? '<span class="badge green">已配置</span>' : '<span class="badge gray">未配置</span>';
        tr.innerHTML = `<td><b>${item.key}</b></td><td>${status}</td>`;
        tbody.appendChild(tr);
      });
    } catch(e) { console.error(e); }
  }

  async function saveGeneral() {
    try {
      await api("/api/settings/general", "POST", { target_language: document.getElementById("gen_target_lang").value });
      alert("Saved.");
    } catch(e) { alert("Error: " + e); }
  }

  function newProvider() {
    document.getElementById("editor_card").style.display = "block";
    document.getElementById("p_id").value = "";
    document.getElementById("p_id").disabled = false;
    document.getElementById("p_type").value = "openai_compatible";
    document.getElementById("p_url").value = "";
    document.getElementById("p_model").value = "";
    document.getElementById("p_env").value = "";
    document.getElementById("p_timeout").value = "60";
    document.getElementById("p_retries").value = "3";
    document.getElementById("p_temp").value = "0.2";
    document.getElementById("editor_msg").textContent = "";
    document.getElementById("editor_card").scrollIntoView({behavior: 'smooth'});
  }

  function editProvider(id, p) {
    document.getElementById("editor_card").style.display = "block";
    document.getElementById("p_id").value = id;
    document.getElementById("p_id").disabled = true; 
    document.getElementById("p_type").value = p.type || "openai_compatible";
    document.getElementById("p_url").value = p.base_url || "";
    document.getElementById("p_model").value = p.model || "";
    document.getElementById("p_env").value = p.api_key_env || "";
    document.getElementById("p_timeout").value = p.timeout_seconds || 60;
    document.getElementById("p_retries").value = p.max_retries || 3;
    document.getElementById("p_temp").value = p.temperature || 0.2;
    document.getElementById("editor_msg").textContent = "";
    document.getElementById("editor_card").scrollIntoView({behavior: 'smooth'});
  }

  async function saveProvider() {
    const body = {
      id: document.getElementById("p_id").value,
      type: document.getElementById("p_type").value,
      base_url: document.getElementById("p_url").value,
      model: document.getElementById("p_model").value,
      api_key_env: document.getElementById("p_env").value,
      timeout_seconds: Number(document.getElementById("p_timeout").value),
      max_retries: Number(document.getElementById("p_retries").value),
      temperature: Number(document.getElementById("p_temp").value)
    };
    try {
      await api("/api/settings/provider", "POST", body);
      document.getElementById("editor_msg").textContent = "Saved!";
      document.getElementById("editor_msg").style.color = "var(--success)";
      loadConfig();
      loadKeys();
    } catch(e) { alert("Error: " + e); }
  }

  async function deleteProvider() {
    const id = document.getElementById("p_id").value;
    if (!id) return;
    if (!confirm("Delete " + id + "?")) return;
    try {
      await api("/api/settings/provider?id=" + encodeURIComponent(id), "DELETE");
      document.getElementById("editor_card").style.display = "none";
      loadConfig();
      loadKeys();
    } catch(e) { alert("Error: " + e); }
  }

  async function testProvider() {
    const id = document.getElementById("p_id").value;
    if (!id) return;
    const msg = document.getElementById("editor_msg");
    msg.textContent = "Testing...";
    msg.style.color = "var(--text-muted)";
    try {
      const res = await api("/api/settings/provider/test", "POST", { provider_id: id });
      msg.textContent = "OK: " + res.translated_text;
      msg.style.color = "var(--success)";
    } catch(e) { 
      msg.textContent = "Failed: " + e; 
      msg.style.color = "var(--danger)";
    }
  }

  async function saveKey() {
    const key = document.getElementById("k_name").value;
    const val = document.getElementById("k_val").value;
    if (!key) { alert("Variable name required."); return; }
    try {
      await api("/api/settings/env", "POST", { key, value: val });
      document.getElementById("k_val").value = "";
      alert("Saved.");
      loadKeys();
      loadConfig();
    } catch(e) { alert("Error: " + e); }
  }

  loadAll();
</script>
</body>
</html>
"""


class GeneralSettingsRequest(BaseModel):
    target_language: str | None = None
    cache_db: str | None = None


class EnvKeyRequest(BaseModel):
    key: str
    value: str


class ProviderTestRequest(BaseModel):
    provider_id: str


def create_settings_app() -> FastAPI:
    app = FastAPI(title="AutoGame Localizer Settings")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        portal_url = os.environ.get("AGL_PORTAL_URL", "http://127.0.0.1:8300/")
        return PAGE.replace("__PORTAL_URL__", portal_url)

    @app.get("/api/settings/config")
    def api_settings_config():
        try:
            return config_manager.get_public_settings()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/settings/general")
    def api_settings_general(body: GeneralSettingsRequest):
        try:
            config_manager.save_general_settings(
                target_language=body.target_language,
                cache_db=body.cache_db,
            )
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/settings/provider")
    def api_settings_provider_save(payload: Dict[str, Any] = Body(...)):
        try:
            config_manager.upsert_provider(payload)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/settings/provider")
    def api_settings_provider_delete(
        id: str = Query(...),
    ):
        try:
            config_manager.delete_provider(id)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/settings/provider/test")
    def api_settings_provider_test(body: ProviderTestRequest):
        try:
            return config_manager.test_provider(body.provider_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/settings/env")
    def api_settings_env():
        try:
            return {
                "items": config_manager.list_env_keys(),
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/settings/env")
    def api_settings_env_save(body: EnvKeyRequest):
        try:
            config_manager.save_env_variable(body.key, body.value)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 12: settings web page"
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8100,
    )

    args = parser.parse_args()

    app = create_settings_app()

    print("Opening AutoGame Localizer Settings")
    print(f"Visit: http://{args.host}:{args.port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()