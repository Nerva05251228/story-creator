$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvActivate = Join-Path $projectRoot 'venv\Scripts\Activate.ps1'
$backendDir = Join-Path $projectRoot 'backend'
$databaseUrl = 'postgresql://postgres:123456@127.0.0.1:5432/story_creator_20260310'
$bananaImageApiToken = 'sk-hrST9TrSTxknWmlcgZN6VaUvkja0qIZ3BXnaDanOz1g'

if (-not (Test-Path $venvActivate)) {
    throw "Virtual environment activation script not found: $venvActivate"
}

if (-not (Test-Path (Join-Path $backendDir 'run_pollers.py'))) {
    throw "Poller entrypoint not found: $(Join-Path $backendDir 'run_pollers.py')"
}

. $venvActivate
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

python .\preflight.py check
if ($LASTEXITCODE -ne 0) {
    throw "Startup preflight check failed with exit code $LASTEXITCODE"
}

python .\run_pollers.py
