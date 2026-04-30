$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $projectRoot 'backend'
$pythonExe = Join-Path $projectRoot 'venv\Scripts\python.exe'
$webScript = Join-Path $projectRoot 'start_web.ps1'
$pollerScript = Join-Path $projectRoot 'start_poller.ps1'
$envHelper = Join-Path $projectRoot 'start_env.ps1'

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

if (-not (Test-Path $envHelper)) {
    throw "Environment helper not found: $envHelper"
}

. $envHelper
Load-ProjectEnv -ProjectRoot $projectRoot | Out-Null
Set-DefaultEnv -Name 'PYTHONUTF8' -Value '1'
Require-Env -Name 'DATABASE_URL'

$env:PYTHONUTF8 = '1'
$env:APP_ROLE = 'preflight'
$env:ENABLE_BACKGROUND_POLLER = '0'

Write-Host 'Running startup preflight...'
Write-SafeEnvSummary -Keys @(
    'DATABASE_URL',
    'IMAGE_PLATFORM_API_TOKEN',
    'TEXT_RELAY_API_KEY',
    'VIDEO_API_TOKEN'
)

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
    -ArgumentList @('-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $webScript)

Start-Sleep -Seconds 1

Start-Process -FilePath 'powershell.exe' `
    -WorkingDirectory $projectRoot `
    -ArgumentList @('-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $pollerScript)

Write-Host 'Web and Poller windows launched.'
