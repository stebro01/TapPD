# If execution is blocked, run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
Set-Location $PSScriptRoot

# ── --install: Create desktop shortcut and exit ──────────────────
if ($args -contains "--install") {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath("Desktop"), "TapPD.lnk"))
    $lnk.TargetPath = "$PSScriptRoot\start.bat"
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = "$PSScriptRoot\assets\tappd.ico,0"
    $lnk.WindowStyle = 1
    $lnk.Description = "TapPD - Contactless Motor Analysis"
    $lnk.Save()
    Write-Host "Desktop shortcut created: TapPD.lnk"
    exit 0
}

if (-not (Test-Path .venv)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    & .venv\Scripts\pip install -r requirements.txt
}
& .venv\Scripts\Activate.ps1

# Copy leapc_cffi from Ultraleap SDK if not present locally
if (-not (Test-Path leapc_cffi)) {
    $sdkPath = "C:\Program Files\Ultraleap\LeapSDK\leapc_cffi"
    if (Test-Path $sdkPath) {
        Write-Host "Copying LeapC bindings from SDK..."
        Copy-Item -Recurse $sdkPath leapc_cffi
    } else {
        Write-Host "WARNING: leapc_cffi not found. Install Ultraleap Tracking from:"
        Write-Host "  https://www.ultraleap.com/downloads/leap-controller/"
    }
}

# Rename .pyd for current Python version if needed
if (Test-Path leapc_cffi) {
    $pyVer = python -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"
    $existing = Get-ChildItem leapc_cffi -Filter "_leapc_cffi.$pyVer-win_amd64.pyd" -ErrorAction SilentlyContinue
    if (-not $existing) {
        $source = Get-ChildItem leapc_cffi -Filter "_leapc_cffi.cp*-win_amd64.pyd" | Select-Object -First 1
        if ($source) {
            $target = "leapc_cffi\_leapc_cffi.$pyVer-win_amd64.pyd"
            Write-Host "Copying $($source.Name) -> _leapc_cffi.$pyVer-win_amd64.pyd"
            Copy-Item $source.FullName $target
        }
    }
}

$env:PATH = "$PSScriptRoot\leapc_cffi;$env:PATH"
python main.py @args
