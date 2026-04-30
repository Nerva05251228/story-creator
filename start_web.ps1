$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvActivate = Join-Path $projectRoot 'venv\Scripts\Activate.ps1'
$backendDir = Join-Path $projectRoot 'backend'
$envHelper = Join-Path $projectRoot 'start_env.ps1'

if (-not (Test-Path $venvActivate)) {
    throw "Virtual environment activation script not found: $venvActivate"
}

if (-not (Test-Path (Join-Path $backendDir 'main.py'))) {
    throw "Backend entrypoint not found: $(Join-Path $backendDir 'main.py')"
}

if (-not (Test-Path $envHelper)) {
    throw "Environment helper not found: $envHelper"
}

. $envHelper
Load-ProjectEnv -ProjectRoot $projectRoot | Out-Null
Set-DefaultEnv -Name 'HOST' -Value '0.0.0.0'
Set-DefaultEnv -Name 'PORT' -Value '10001'
Set-DefaultEnv -Name 'WEB_CONCURRENCY' -Value '4'
Set-DefaultEnv -Name 'PYTHONUTF8' -Value '1'
Require-Env -Name 'DATABASE_URL'

. $venvActivate
Set-Location $backendDir

$env:APP_ROLE = 'web'
$env:ENABLE_BACKGROUND_POLLER = '0'
$host.UI.RawUI.WindowTitle = 'story_creator - web'

Write-Host "Starting Web service..."
Write-SafeEnvSummary -Keys @(
    'DATABASE_URL',
    'HOST',
    'PORT',
    'WEB_CONCURRENCY',
    'IMAGE_PLATFORM_API_TOKEN',
    'TEXT_RELAY_API_KEY',
    'VIDEO_API_TOKEN'
)

python .\preflight.py migrate
if ($LASTEXITCODE -ne 0) {
    throw "Startup preflight failed with exit code $LASTEXITCODE"
}

python -m uvicorn main:app --host $env:HOST --port $env:PORT --workers $env:WEB_CONCURRENCY
