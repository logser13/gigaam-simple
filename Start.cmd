@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto setup
if not exist ".venv\Lib\site-packages\silero_vad\__init__.py" goto setup
if not exist "models\v3_e2e_ctc.ckpt" goto setup
if not exist "models\v3_e2e_ctc_tokenizer.model" goto setup
start "" ".venv\Scripts\pythonw.exe" "app.py"
exit /b
:setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "app.py"
