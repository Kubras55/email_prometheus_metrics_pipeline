@echo off
setlocal
set "PROJECT_ROOT=%~dp0"

if not exist "%PROJECT_ROOT%grafana\runtime\data" mkdir "%PROJECT_ROOT%grafana\runtime\data"
if not exist "%PROJECT_ROOT%grafana\runtime\logs" mkdir "%PROJECT_ROOT%grafana\runtime\logs"
if not exist "%PROJECT_ROOT%grafana\runtime\plugins" mkdir "%PROJECT_ROOT%grafana\runtime\plugins"
cd /d "%PROJECT_ROOT%tools\grafana-13.1.1"
"bin\grafana.exe" server ^
  --homepath="%PROJECT_ROOT%tools\grafana-13.1.1" ^
  --config="%PROJECT_ROOT%grafana\custom.ini"

if errorlevel 1 pause
