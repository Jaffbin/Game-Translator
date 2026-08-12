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
  --collect-submodules agl `
  --hidden-import phase75_web `
  --hidden-import phase8_launcher `
  app_main.py

Write-Host "Debug build complete."
Write-Host "EXE folder: dist/AutoGameLocalizerDebug"