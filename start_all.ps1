$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $projectRoot 'backend'
$pythonExe = Join-Path $projectRoot 'venv\Scripts\python.exe'
$webScript = Join-Path $projectRoot 'start_web.ps1'
$pollerScript = Join-Path $projectRoot 'start_poller.ps1'
$databaseUrl = 'postgresql://postgres:123456@127.0.0.1:5432/story_creator_20260310'
$bananaImageApiToken = 'sk-hrST9TrSTxknWmlcgZN6VaUvkja0qIZ3BXnaDanOz1g'

if (-not (Test-Path $webScript)) {
    throw "Web startup script not found: $webScript"
}

if (-not (Test-Path $pollerScript)) {
    throw "Poller startup script not found: $pollerScript"
}

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

if (-not (Test-Path (Join-Path $backendDir 'preflight.py'))) {
    throw "Startup preflight not found: $(Join-Path $backendDir 'preflight.py')"
}

$env:DATABASE_URL = $databaseUrl
$env:BANANA_IMAGE_API_TOKEN = $bananaImageApiToken
$env:PYTHONUTF8 = '1'
$env:APP_ROLE = 'preflight'
$env:ENABLE_BACKGROUND_POLLER = '0'

Push-Location $backendDir
try {
    & $pythonExe .\preflight.py migrate
    if ($LASTEXITCODE -ne 0) {
        throw "Startup preflight failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Start-Process -FilePath 'powershell.exe' `
    -WorkingDirectory $projectRoot `
    -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $webScript)

Start-Sleep -Seconds 1

Start-Process -FilePath 'powershell.exe' `
    -WorkingDirectory $projectRoot `
    -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $pollerScript)

Write-Host 'Web and Poller windows launched.'
