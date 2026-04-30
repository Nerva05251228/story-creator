$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvActivate = Join-Path $projectRoot 'venv\Scripts\Activate.ps1'
$backendDir = Join-Path $projectRoot 'backend'
$envHelper = Join-Path $projectRoot 'start_env.ps1'

if (-not (Test-Path $venvActivate)) {
    throw "Virtual environment activation script not found: $venvActivate"
}

if (-not (Test-Path (Join-Path $backendDir 'run_pollers.py'))) {
    throw "Poller entrypoint not found: $(Join-Path $backendDir 'run_pollers.py')"
}

if (-not (Test-Path $envHelper)) {
    throw "Environment helper not found: $envHelper"
}

. $envHelper
Load-ProjectEnv -ProjectRoot $projectRoot | Out-Null
Set-DefaultEnv -Name 'PYTHONUTF8' -Value '1'
Require-Env -Name 'DATABASE_URL'

. $venvActivate
Set-Location $backendDir

$env:APP_ROLE = 'poller'
$env:ENABLE_BACKGROUND_POLLER = '1'
$host.UI.RawUI.WindowTitle = 'story_creator - poller'

Write-Host "Starting Poller..."
Write-SafeEnvSummary -Keys @(
    'DATABASE_URL',
    'IMAGE_PLATFORM_API_TOKEN',
    'TEXT_RELAY_API_KEY',
    'VIDEO_API_TOKEN'
)

python .\preflight.py check
if ($LASTEXITCODE -ne 0) {
    throw "Startup preflight check failed with exit code $LASTEXITCODE"
}

python .\run_pollers.py
