@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -u prepare_alternative.py
if errorlevel 1 (
  echo Download failed. Check the internet connection and try again.
  pause
  exit /b 1
)
echo Ready. Open Start.cmd and select the alternative model.
pause
