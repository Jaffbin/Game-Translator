# AutoGame Localizer

AutoGame Localizer is a Windows-first, local desktop workflow for translating
games safely. It combines engine-aware text extraction, cloud translation,
placeholder protection, QA, patch preview, automatic backup, installation, and
rollback.

## Current v20 focus

- Simple player flow for personal use
- RPG Maker MV/MZ first, then Ren'Py, then lightweight Unity text formats
- Cloud OpenAI-compatible providers first
- Local Ollama fallback without an API key
- Existing project and patch formats remain compatible

RPG Maker MV/MZ support covers database text, system/menu terms, message and
scrolling text, choices, speaker/actor names, and a conservative allowlist of
player-facing plugin parameters. Unknown plugin fields are left unchanged to
avoid translating scripts, identifiers, asset names, or configuration values.

Ren'Py support reads both `old`/`new` string templates and generated dialogue
translation blocks under `game/tl`, including speaker expressions and existing
translations. Original script files are not rewritten when translation files
are available.

The advanced project console also includes an offline RPG Maker data modifier
for allowlisted actor, item, equipment, enemy, skill, and state values. It
creates an ordinary previewable patch; installation still uses automatic backup
and supports the same rollback workflow. Scripts, notes, plugin code, and live
memory editing are deliberately outside this modifier.

## Safe player workflow

```text
Choose game → Detect → Scan and preview → Confirm cloud translation
→ QA → Preview patch → Confirm install → Automatic backup → Rollback if needed
```

The player flow never silently uses the mock provider and never modifies the game
before explicit confirmation.

Choosing a game also runs read-only diagnostics for text encoding, bundled font
files, runtime markers, and available disk space. Non-UTF-8 text is reported
before scanning rather than silently dropping invalid bytes. Font coverage is
currently reported as unverified; check the translated text inside the game.
On Windows, the folder button uses the Explorer-style folder dialog. Selecting
the game's parent or its `data` folder is accepted when one unambiguous game can
be found nearby; otherwise the player shows which folder layout it expects.

## Gemini API

The Gemini 3.5 Flash-Lite preset uses Google's OpenAI-compatible text endpoint.
Configure your own `GEMINI_API_KEY` in Settings; the key is saved in Windows
Credential Manager when available, with the ignored local `.env` file as a
fallback. In packaged Windows builds the fallback lives under
`%LOCALAPPDATA%\AutoGameLocalizer\.env`, so replacing the EXE does not discard
the key. Existing per-build `.env` files remain readable during migration.
Projects, settings, and translation memory in packaged Windows builds now live
under `%LOCALAPPDATA%\AutoGameLocalizer` as well. On first launch, the app copies
projects from adjacent older `release/<build>/AutoGameLocalizer` folders into
this stable location without deleting the originals. The Projects page displays
the active project storage path.
The free API tier has usage limits and may use submitted content to
improve Google's products. Check current terms before sending game text.

## Local Ollama

The bundled `ollama` provider targets
`http://127.0.0.1:11434/v1/chat/completions` and defaults to
`qwen2.5:7b`. Start Ollama and install that model (or change the model in
Settings), then use **Test Connection**. Player mode selects a configured cloud
provider first and falls back to Ollama only while its local endpoint is running.

## Run from source

```powershell
python -m pip install -r requirements.txt
python app_main.py
```

Developer dependencies and tests:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

To open an existing project console directly:

```powershell
python app_main.py projects\MyGame_zh
```

## Data and privacy

Project data stays on the local machine. Text is sent outside the computer only
when a cloud provider is selected and only after confirmation in player mode. On Windows, new
API keys are stored in Credential Manager; `.env` remains supported for existing
source installations.

See [the architecture notes](docs/architecture.md) for module boundaries and the
compatibility policy.
