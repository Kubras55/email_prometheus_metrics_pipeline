@echo off
setlocal
cd /d "%~dp0"

if not exist "prometheus\data_current" mkdir "prometheus\data_current"

"tools\prometheus-3.13.1.windows-amd64\prometheus.exe" ^
  --config.file="prometheus\prometheus.yml" ^
  --storage.tsdb.path="prometheus\data_current" ^
  --web.listen-address="127.0.0.1:9091"

if errorlevel 1 pause
