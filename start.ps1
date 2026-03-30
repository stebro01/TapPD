# If execution is blocked, run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    & .venv\Scripts\pip install -r requirements.txt
}
& .venv\Scripts\Activate.ps1
$env:PATH = "$PSScriptRoot\leapc_cffi;$env:PATH"
python main.py @args
