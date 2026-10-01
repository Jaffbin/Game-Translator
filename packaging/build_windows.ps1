Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "Installing/updating build dependencies..."

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install "pyinstaller>=6,<7"

Write-Host "Building AutoGameLocalizer..."

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name AutoGameLocalizer `
  --collect-all fastapi `
  --collect-all uvicorn `
  --collect-all pydantic `
  --collect-all starlette `
  --collect-all anyio `
  --collect-all keyring `
  --collect-all multipart `
  --collect-all python_multipart `
  --collect-submodules agl `
  --hidden-import agl.api.launcher `
  --hidden-import agl.api.console `
  --hidden-import agl.api.project_console `
  app_main.py

Write-Host "Preparing release folder..."

New-Item -ItemType Directory -Force "dist/AutoGameLocalizer/projects" | Out-Null
New-Item -ItemType Directory -Force "dist/AutoGameLocalizer/patches" | Out-Null

if (Test-Path "config.example.toml") {
    Copy-Item "config.example.toml" "dist/AutoGameLocalizer/config.example.toml" -Force
}

if (Test-Path ".env.example") {
    Copy-Item ".env.example" "dist/AutoGameLocalizer/.env.example" -Force
}

$readme = @"
AutoGame Localizer
==================

1. Double click AutoGameLocalizer.exe
2. Choose the recommended Player mode
3. Open Settings and configure a cloud Provider + API key
4. Choose a supported game folder
5. Scan, review the estimate, confirm translation, then apply the patch

The Player workflow never uses mock translations. It asks before sending text
to the cloud and again before changing game files. A backup is created before
installation and can be restored from Player mode.

API keys are stored in Windows Credential Manager when available. Existing .env
installations remain supported; never distribute a real .env file.
"@

Set-Content -Path "dist/AutoGameLocalizer/README.txt" -Value $readme -Encoding UTF8

Write-Host "Creating release zip..."

New-Item -ItemType Directory -Force "release" | Out-Null

if (Test-Path "release/AutoGameLocalizer.zip") {
    Remove-Item "release/AutoGameLocalizer.zip" -Force
}

Compress-Archive -Path "dist/AutoGameLocalizer" -DestinationPath "release/AutoGameLocalizer.zip"

Write-Host "Build complete."
Write-Host "EXE folder: dist/AutoGameLocalizer"
Write-Host "Release zip: release/AutoGameLocalizer.zip"
