$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot 'venv\Scripts\python.exe'
$backendDir = Join-Path $projectRoot 'backend'
$databaseUrl = 'postgresql://postgres:123456@127.0.0.1:5432/story_creator_20260310'
$bananaImageApiToken = 'sk-hrST9TrSTxknWmlcgZN6VaUvkja0qIZ3BXnaDanOz1g'

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

if (-not (Test-Path (Join-Path $backendDir 'run_pollers.py'))) {
    throw "Poller entrypoint not found: $(Join-Path $backendDir 'run_pollers.py')"
}

Set-Location $backendDir

$env:DATABASE_URL = $databaseUrl
$env:BANANA_IMAGE_API_TOKEN = $bananaImageApiToken
$env:PYTHONUTF8 = '1'
$env:APP_ROLE = 'poller'
$env:ENABLE_BACKGROUND_POLLER = '1'
$host.UI.RawUI.WindowTitle = 'story_creator - poller'

Write-Host "Starting Poller..."
Write-Host "DATABASE_URL=$databaseUrl"
Write-Host "BANANA_IMAGE_API_TOKEN=[set]"

& $pythonExe .\preflight.py migrate
if ($LASTEXITCODE -ne 0) {
    throw "Startup preflight migrate failed with exit code $LASTEXITCODE"
}

& $pythonExe .\run_pollers.py
