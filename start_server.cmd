@echo off
setlocal

cd /d "%~dp0backend" || exit /b 1

set "DATABASE_URL=postgresql://postgres:123456@127.0.0.1:5432/story_creator_20260310"
set "WEB_CONCURRENCY=4"
set "PORT=10001"
set "PYTHONUTF8=1"
set "APP_ROLE=web"
set "ENABLE_BACKGROUND_POLLER=0"

echo Starting main server on port %PORT% with %WEB_CONCURRENCY% workers...
"%~dp0venv\Scripts\python.exe" preflight.py migrate
if errorlevel 1 exit /b %errorlevel%
"%~dp0venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port %PORT% --workers %WEB_CONCURRENCY%
