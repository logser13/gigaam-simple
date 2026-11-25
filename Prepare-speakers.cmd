@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Start.cmd once before preparing speaker models.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip --disable-pip-version-check install --only-binary=:all: sherpa-onnx==1.12.40
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -u prepare_speakers.py
if errorlevel 1 goto failed
echo Ready. Open Start.cmd and enable speaker separation.
pause
exit /b 0
:failed
echo Setup failed. Check the internet connection and try again.
pause
exit /b 1
