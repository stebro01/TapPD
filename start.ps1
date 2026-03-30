# If execution is blocked, run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
Set-Location $PSScriptRoot
& .venv\Scripts\Activate.ps1
$env:PATH = "$PSScriptRoot\leapc_cffi;$env:PATH"
python main.py @args
