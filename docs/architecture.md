# AutoGame Localizer — v20 Architecture

## Canonical runtime

```text
app_main.py
    |
    +-- agl.api.launcher          one localhost Uvicorn server + pywebview
            |
            +-- /                 first-run portal
            +-- /player           safe player workflow
            +-- /workspace        project management
            +-- /settings         providers and secrets
            +-- /console/<id>     dynamically mounted project console
                    |
                    +-- agl.services
                            +-- scan
                            +-- translation
                            +-- quality
                            +-- delivery
                            +-- rpgmaker_modding
                            +-- player_workflow
                                    |
                                    +-- agl.engines
                                    +-- agl.project / agl.cache
                                    +-- agl.providers / agl.pipeline
```

The desktop runtime uses one HTTP server and one origin. Opening a project mounts
its console into the root application; it does not create another Uvicorn thread
or consume another port. The standalone workspace follows the same rule and
mounts project consoles into its existing FastAPI application.

## Player safety contract

The simple workflow is intentionally staged:

1. detect the game engine and configured cloud provider;
2. scan locally and show entry/character/token estimates;
3. obtain explicit confirmation before sending text to a cloud provider;
4. translate with resumable per-entry persistence;
5. run QA and generate a patch;
6. obtain explicit confirmation before modifying the game;
7. create a backup, install, and offer rollback.

Mock translations are never used by the player workflow. They remain available
for automated tests and developer-mode experiments.

Provider selection prefers a configured cloud endpoint. If none is available,
the bundled keyless Ollama preset is eligible only while its localhost endpoint
is reachable; otherwise Player remains in configuration-required state.

## Compatibility

The `phase*.py` modules and root UI/operations shims remain compatibility APIs in
v20. New runtime code must use `agl.api`, `agl.services`, and `ui`. Existing
`project.json`, `entries.db`, configuration, patch, and CSV formats remain
readable. SQLite changes use an explicit schema version and a one-time pre-v2
database backup.

## Persistence

- Project metadata: `project.json`
- Entries, history, and revisions: project `entries.db`
- Translation memory: `.cache/translation.db`
- Glossary: project `glossary.csv`
- Patch and rollback data: project `patches/` and `backups/`
- RPG Maker data mods: ordinary project patches with `kind: rpgmaker_data_mod`
- Ren'Py dialogue: generated comment/target pairs and `old`/`new` strings under `game/tl`
- API secrets: Windows Credential Manager, with `.env` as a compatibility fallback
- Packaged Windows user data: `%LOCALAPPDATA%/AutoGameLocalizer`, independent of
  the EXE location; older adjacent release builds are copied on first launch
  without overwriting existing projects or deleting source folders

## Migration direction

`agl.operations` is now a compatibility facade over focused implementations in
`agl.services`. Phase modules can be removed only after their public entry points
have completed a deprecation cycle.
