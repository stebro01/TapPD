# Motryx launcher (Windows).
# If execution is blocked, run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
#   .\start.ps1              webcam tracking (default on this branch)
#   .\start.ps1 --mock       simulation source, no camera needed
#   .\start.ps1 --leap       also set up and enable the Leap Motion path
#   .\start.ps1 --install    create a desktop shortcut and exit

Set-Location $PSScriptRoot

# -- --install: create desktop shortcut and exit -------------------
if ($args -contains "--install") {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath("Desktop"), "Motryx.lnk"))
    $lnk.TargetPath = "$PSScriptRoot\start.bat"
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = "$PSScriptRoot\assets\tappd.ico,0"
    $lnk.WindowStyle = 1
    $lnk.Description = "Motryx - Movement Lab"
    $lnk.Save()
    Write-Host "Desktop shortcut created: Motryx.lnk"
    exit 0
}

# Flags handled here must not reach main.py.
$useLeap = $args -contains "--leap"
$appArgs = @($args | Where-Object { $_ -notin @("--leap", "--install") })

# -- main app venv (Python 3.12+) ----------------------------------
if (-not (Test-Path .venv)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    & .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
}

# -- MediaPipe sidecar (separate Python 3.12 venv) -----------------
# Webcam tracking runs out-of-process; without this there is no camera source.
$sidecarPy    = ".\mediapipe_sidecar\.venv\Scripts\python.exe"
$sidecarModel = ".\mediapipe_sidecar\models\hand_landmarker.task"
if (-not (Test-Path $sidecarPy) -or -not (Test-Path $sidecarModel)) {
    Write-Host "Setting up MediaPipe sidecar (one-time, downloads ~200 MB)..."
    & "$PSScriptRoot\mediapipe_sidecar\setup_sidecar.ps1"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: sidecar setup failed - webcam tracking will be unavailable." -ForegroundColor Yellow
    }
}

# -- Leap Motion (opt-in) ------------------------------------------
# The Leap path is off by default on this branch; capture/__init__.py reads
# MOTRYX_ENABLE_LEAP. Everything below only runs with --leap.
if ($useLeap) {
    $env:MOTRYX_ENABLE_LEAP = "1"

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

    # The SDK ships one .pyd per Python version; copy it to the name ours wants.
    if (Test-Path leapc_cffi) {
        $pyVer = & .venv\Scripts\python.exe -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"
        $existing = Get-ChildItem leapc_cffi -Filter "_leapc_cffi.$pyVer-win_amd64.pyd" -ErrorAction SilentlyContinue
        if (-not $existing) {
            $source = Get-ChildItem leapc_cffi -Filter "_leapc_cffi.cp*-win_amd64.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($source) {
                Write-Host "Copying $($source.Name) -> _leapc_cffi.$pyVer-win_amd64.pyd"
                Copy-Item $source.FullName "leapc_cffi\_leapc_cffi.$pyVer-win_amd64.pyd"
            }
        }
        $env:PATH = "$PSScriptRoot\leapc_cffi;$env:PATH"
    }
}

& .venv\Scripts\python.exe main.py @appArgs
