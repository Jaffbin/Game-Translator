Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "Building AutoGameLocalizer DEBUG version..."

python -m PyInstaller `
  --noconfirm `
  --clean `
  --console `
  --name AutoGameLocalizerDebug `
  --collect-all fastapi `
  --collect-all uvicorn `
  --collect-all pydantic `
  --collect-all starlette `
  --collect-all anyio `
  --collect-all multipart `
  --collect-all python_multipart `
  --collect-all keyring `
  --collect-submodules agl `
  --hidden-import agl.api.launcher `
  --hidden-import agl.api.console `
  --hidden-import agl.api.project_console `
  app_main.py

Write-Host "Debug build complete."
Write-Host "EXE folder: dist/AutoGameLocalizerDebug"
