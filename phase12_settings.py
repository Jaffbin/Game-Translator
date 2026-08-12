from __future__ import annotations

import argparse
from typing import Any, Dict

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agl import config_manager


PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoGame Localizer Settings</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 16px;
      background: #f7f7f7;
    }

    h1 {
      margin: 0 0 12px 0;
      font-size: 22px;
    }

    h2 {
      font-size: 16px;
      margin: 0 0 8px 0;
    }

    .panel {
      background: #fff;
      border: 1px solid #ddd;
      padding: 10px;
      margin-bottom: 12px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
    }

    th, td {
      border: 1px solid #ddd;
      padding: 6px;
      font-size: 13px;
      vertical-align: top;
    }

    th {
      background: #efefef;
      text-align: left;
    }

    input, select {
      padding: 6px;
      margin-right: 6px;
    }

    button {
      padding: 6px 10px;
      cursor: pointer;
    }

    .ok {
      color: green;
      font-weight: bold;
    }

    .error {
      color: red;
      font-weight: bold;
    }

    .small {
      color: #666;
      font-size: 12px;
    }

    .form-grid {
      display: grid;
      grid-template-columns: 180px 420px;
      gap: 6px;
      align-items: center;
    }
  </style>
</head>
<body>
  <h1>AutoGame Localizer Settings</h1>

  <div class="panel">
    <h2>General</h2>
    <div>
      <label>Default target language</label>
      <input id="target_language" type="text" value="zh-CN">
      <button onclick="saveGeneral()">Save General</button>
      <span id="message"></span>
    </div>
  </div>

  <div class="panel">
    <h2>Providers</h2>
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Type</th>
          <th>Base URL</th>
          <th>Model</th>
          <th>API Key Env</th>
          <th>Has Key</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="providers"></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Provider Editor</h2>
    <div class="form-grid">
      <div>Provider ID</div>
      <input id="p_id" type="text" placeholder="nvidia / deepseek / openrouter">

      <div>Type</div>
      <select id="p_type">
        <option value="openai_compatible">openai_compatible</option>
        <option value="mock">mock</option>
      </select>

      <div>Base URL</div>
      <input id="p_base_url" type="text" placeholder="https://api.example.com/v1/chat/completions" style="width:520px;">

      <div>Model</div>
      <input id="p_model" type="text" placeholder="model name" style="width:520px;">

      <div>API Key Env</div>
      <input id="p_api_key_env" type="text" placeholder="OPENROUTER_API_KEY">

      <div>Timeout seconds</div>
      <input id="p_timeout" type="text" value="60">

      <div>Max retries</div>
      <input id="p_retries" type="text" value="3">

      <div>Retry backoff seconds</div>
      <input id="p_backoff" type="text" value="1.5">

      <div>Temperature</div>
      <input id="p_temperature" type="text" value="0.2">
    </div>

    <div style="margin-top:8px;">
      <button onclick="newProvider()">New</button>
      <button onclick="saveProvider()">Save Provider</button>
      <button onclick="deleteCurrentProvider()">Delete Provider</button>
      <button onclick="testCurrentProvider()">Test Provider</button>
    </div>
  </div>

  <div class="panel">
    <h2>API Keys (.env)</h2>
    <table>
      <thead>
        <tr>
          <th>Environment Variable</th>
          <th>Has Value</th>
        </tr>
      </thead>
      <tbody id="env_keys"></tbody>
    </table>

    <div style="margin-top:8px;">
      <input id="env_key" type="text" placeholder="NVIDIA_API_KEY" style="width:260px;">
      <input id="env_value" type="password" placeholder="API key" style="width:420px;">
      <button onclick="saveEnvKey()">Save API Key</button>
    </div>

    <div class="small">
      API keys are stored in local .env file. They are never shown in this page.
    </div>
  </div>

  <script>
    let providers = {};

    function setMessage(text, kind) {
      const el = document.getElementById("message");
      el.textContent = text || "";
      el.className = kind || "";
    }

    async function api(url, method = "GET", body = null) {
      const options = {
        method: method,
        headers: {}
      };

      if (body !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }

      const response = await fetch(url, options);
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.detail || response.statusText);
      }

      return data;
    }

    async function loadSettings() {
      const data = await api("/api/settings/config");

      document.getElementById("target_language").value =
        (data.translation || {}).target_language || "zh-CN";

      providers = data.providers || {};

      renderProviders();
      await loadEnvKeys();
    }

    function renderProviders() {
      const tbody = document.getElementById("providers");
      tbody.innerHTML = "";

      Object.entries(providers).forEach(([id, p]) => {
        const tr = document.createElement("tr");

        const tdId = document.createElement("td");
        tdId.textContent = id;

        const tdType = document.createElement("td");
        tdType.textContent = p.type || "";

        const tdBaseUrl = document.createElement("td");
        tdBaseUrl.textContent = p.base_url || "";

        const tdModel = document.createElement("td");
        tdModel.textContent = p.model || "";

        const tdEnv = document.createElement("td");
        tdEnv.textContent = p.api_key_env || "";

        const tdHasKey = document.createElement("td");
        tdHasKey.textContent = p.has_api_key ? "yes" : "no";

        const tdActions = document.createElement("td");

        const editButton = document.createElement("button");
        editButton.textContent = "Edit";
        editButton.onclick = () => editProvider(id);

        const testButton = document.createElement("button");
        testButton.textContent = "Test";
        testButton.onclick = () => testProvider(id);

        tdActions.appendChild(editButton);
        tdActions.appendChild(document.createTextNode(" "));
        tdActions.appendChild(testButton);

        tr.appendChild(tdId);
        tr.appendChild(tdType);
        tr.appendChild(tdBaseUrl);
        tr.appendChild(tdModel);
        tr.appendChild(tdEnv);
        tr.appendChild(tdHasKey);
        tr.appendChild(tdActions);

        tbody.appendChild(tr);
      });
    }

    async function loadEnvKeys() {
      const data = await api("/api/settings/env");

      const tbody = document.getElementById("env_keys");
      tbody.innerHTML = "";

      (data.items || []).forEach(item => {
        const tr = document.createElement("tr");

        const tdKey = document.createElement("td");
        tdKey.textContent = item.key;

        const tdHas = document.createElement("td");
        tdHas.textContent = item.has_value ? "yes" : "no";

        tr.appendChild(tdKey);
        tr.appendChild(tdHas);

        tbody.appendChild(tr);
      });

      const select = document.getElementById("env_key");

      // Keep current value if user typed one.
      const current = select.value;

      select.innerHTML = "";

      (data.items || []).forEach(item => {
        const option = document.createElement("option");
        option.value = item.key;
        option.textContent = item.key;
        select.appendChild(option);
      });

      if (current) {
        select.value = current;
      }
    }

    async function saveGeneral() {
      try {
        const targetLanguage =
          document.getElementById("target_language").value.trim();

        await api("/api/settings/general", "POST", {
          target_language: targetLanguage
        });

        setMessage("General settings saved.", "ok");
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    function newProvider() {
      document.getElementById("p_id").value = "";
      document.getElementById("p_type").value = "openai_compatible";
      document.getElementById("p_base_url").value = "";
      document.getElementById("p_model").value = "";
      document.getElementById("p_api_key_env").value = "";
      document.getElementById("p_timeout").value = "60";
      document.getElementById("p_retries").value = "3";
      document.getElementById("p_backoff").value = "1.5";
      document.getElementById("p_temperature").value = "0.2";

      setMessage("New provider form ready.", "");
    }

    function editProvider(id) {
      const p = providers[id];

      if (!p) {
        return;
      }

      document.getElementById("p_id").value = id;
      document.getElementById("p_type").value = p.type || "openai_compatible";
      document.getElementById("p_base_url").value = p.base_url || "";
      document.getElementById("p_model").value = p.model || "";
      document.getElementById("p_api_key_env").value = p.api_key_env || "";
      document.getElementById("p_timeout").value = p.timeout_seconds ?? 60;
      document.getElementById("p_retries").value = p.max_retries ?? 3;
      document.getElementById("p_backoff").value = p.retry_backoff_seconds ?? 1.5;
      document.getElementById("p_temperature").value = p.temperature ?? 0.2;

      setMessage("Editing provider: " + id, "");
    }

    async function saveProvider() {
      const payload = {
        id: document.getElementById("p_id").value.trim(),
        type: document.getElementById("p_type").value,
        base_url: document.getElementById("p_base_url").value.trim(),
        model: document.getElementById("p_model").value.trim(),
        api_key_env: document.getElementById("p_api_key_env").value.trim(),
        timeout_seconds: Number(document.getElementById("p_timeout").value || 60),
        max_retries: Number(document.getElementById("p_retries").value || 3),
        retry_backoff_seconds: Number(document.getElementById("p_backoff").value || 1.5),
        temperature: Number(document.getElementById("p_temperature").value || 0.2)
      };

      if (!payload.id) {
        setMessage("Provider ID is required.", "error");
        return;
      }

      try {
        await api("/api/settings/provider", "POST", payload);

        setMessage("Provider saved: " + payload.id, "ok");

        await loadSettings();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    function currentProviderId() {
      return document.getElementById("p_id").value.trim();
    }

    async function deleteCurrentProvider() {
      const id = currentProviderId();

      if (!id) {
        setMessage("Provider ID is required.", "error");
        return;
      }

      if (!confirm("Delete provider: " + id + "?")) {
        return;
      }

      try {
        await api("/api/settings/provider?id=" + encodeURIComponent(id), "DELETE");

        setMessage("Provider deleted: " + id, "ok");

        newProvider();
        await loadSettings();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    async function testProvider(id) {
      try {
        setMessage("Testing provider: " + id + "...", "");

        const data = await api("/api/settings/provider/test", "POST", {
          provider_id: id
        });

        setMessage(
          "Test OK: " + data.translated_text,
          "ok"
        );
      } catch (err) {
        setMessage("Test failed: " + err.message, "error");
      }
    }

    function testCurrentProvider() {
      const id = currentProviderId();

      if (!id) {
        setMessage("Provider ID is required.", "error");
        return;
      }

      testProvider(id);
    }

    async function saveEnvKey() {
      const key = document.getElementById("env_key").value.trim();
      const value = document.getElementById("env_value").value;

      if (!key) {
        setMessage("Environment variable name is required.", "error");
        return;
      }

      try {
        await api("/api/settings/env", "POST", {
          key: key,
          value: value
        });

        setMessage("Saved environment variable: " + key, "ok");

        document.getElementById("env_value").value = "";

        await loadEnvKeys();
        await loadSettings();
      } catch (err) {
        setMessage(err.message, "error");
      }
    }

    loadSettings();
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
        return PAGE

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