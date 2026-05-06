$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot 'venv\Scripts\python.exe'
$backendDir = Join-Path $projectRoot 'backend'
$databaseUrl = 'postgresql://postgres:123456@127.0.0.1:5432/story_creator_20260310'
$bananaImageApiToken = 'sk-hrST9TrSTxknWmlcgZN6VaUvkja0qIZ3BXnaDanOz1g'
$webConcurrency = '4'
$port = '10001'

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

if (-not (Test-Path (Join-Path $backendDir 'main.py'))) {
    throw "Backend entrypoint not found: $(Join-Path $backendDir 'main.py')"
}

Set-Location $backendDir

$env:DATABASE_URL = $databaseUrl
$env:BANANA_IMAGE_API_TOKEN = $bananaImageApiToken
$env:WEB_CONCURRENCY = $webConcurrency
$env:PORT = $port
$env:PYTHONUTF8 = '1'
$env:APP_ROLE = 'web'
$env:ENABLE_BACKGROUND_POLLER = '0'
$host.UI.RawUI.WindowTitle = 'story_creator - web'

Write-Host "Starting Web service..."
Write-Host "DATABASE_URL=$databaseUrl"
Write-Host "BANANA_IMAGE_API_TOKEN=[set]"
Write-Host "WEB_CONCURRENCY=$webConcurrency"
Write-Host "PORT=$port"

& $pythonExe .\preflight.py migrate
if ($LASTEXITCODE -ne 0) {
    throw "Startup preflight failed with exit code $LASTEXITCODE"
}

& $pythonExe -m uvicorn main:app --host 0.0.0.0 --port $port --workers $webConcurrency
