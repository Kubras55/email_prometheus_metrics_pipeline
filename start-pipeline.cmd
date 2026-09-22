@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python sanal ortami bulunamadi: .venv\Scripts\python.exe
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m src.main

if errorlevel 1 pause
