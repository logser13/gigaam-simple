$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
if (-not (Test-Path -LiteralPath '.\vendor\GigaAM\pyproject.toml')) {
    git clone https://github.com/salute-developers/GigaAM.git .\vendor\GigaAM
    if ($LASTEXITCODE -ne 0) { throw 'Could not download GigaAM source.' }
    git -C .\vendor\GigaAM checkout 7447938d791c4f3e643386ee22c33777004293a5
    if ($LASTEXITCODE -ne 0) { throw 'Could not select verified source revision.' }
}
& .\.venv\Scripts\python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu --timeout 90 --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Could not install PyTorch.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --timeout 90 --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Could not install dependencies.' }
& .\.venv\Scripts\python.exe prepare_model.py
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare model.' }
Write-Host 'Ready. Open Start.cmd.'
