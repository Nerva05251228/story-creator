@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_poller.ps1" %*
set "START_POLLER_EXIT=%ERRORLEVEL%"
if not "%START_POLLER_EXIT%"=="0" (
    echo.
    echo Startup failed with exit code %START_POLLER_EXIT%.
    echo Review the error output above, then press any key to close this window.
    pause >nul
)
exit /b %START_POLLER_EXIT%
