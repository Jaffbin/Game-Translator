Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "Installing/updating build dependencies..."

python -m pip install --upgrade pip
python -m pip install pyinstaller fastapi uvicorn requests tomli

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
  --collect-all multipart `
  --collect-all python_multipart `
  --collect-submodules agl `
  --hidden-import phase75_web `
  --hidden-import phase8_launcher `
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
2. Create a project
3. Open Web Console
4. Use Scan / Translate / QA / Patch / Install / Rollback

If you want to use real machine translation:

1. Copy .env.example to .env
2. Put your API key into .env
3. Never distribute your real .env file
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