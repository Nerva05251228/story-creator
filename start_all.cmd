@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1" %*
set "START_ALL_EXIT=%ERRORLEVEL%"
if not "%START_ALL_EXIT%"=="0" (
    echo.
    echo Startup failed with exit code %START_ALL_EXIT%.
    echo Review the error output above, then press any key to close this window.
    pause >nul
)
exit /b %START_ALL_EXIT%
