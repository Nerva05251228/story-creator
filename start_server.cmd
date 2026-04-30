@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_web.ps1" %*
set "START_SERVER_EXIT=%ERRORLEVEL%"
if not "%START_SERVER_EXIT%"=="0" (
    echo.
    echo Startup failed with exit code %START_SERVER_EXIT%.
    echo Review the error output above, then press any key to close this window.
    pause >nul
)
exit /b %START_SERVER_EXIT%
