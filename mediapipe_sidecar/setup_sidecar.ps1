# Bootstrap the MediaPipe sidecar on Windows: a Python 3.12 venv (MediaPipe has
# no 3.13/3.14 wheels) plus the hand_landmarker/face_landmarker models.
# Idempotent - safe to re-run.
#
# Windows counterpart of setup_sidecar.sh.
# If execution is blocked, run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# -- locate a Python 3.12 interpreter ------------------------------
$py312 = $null

# 1) the py launcher knows every registered install
if (Get-Command py -ErrorAction SilentlyContinue) {
    $probe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $probe) { $py312 = $probe.Trim() }
}

# 2) fall back to the default per-user / machine-wide install locations
if (-not $py312) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "${env:ProgramFiles(x86)}\Python312\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $py312 = $c; break }
    }
}

if (-not $py312) {
    Write-Host "ERROR: Python 3.12 nicht gefunden." -ForegroundColor Red
    Write-Host "  MediaPipe unterstuetzt kein Python 3.13/3.14 - die Sidecar braucht 3.12."
    Write-Host "  Installieren mit:  winget install --id Python.Python.3.12 --scope user"
    exit 1
}
Write-Host "Verwende Python: $(& $py312 --version) ($py312)"

# -- venv ----------------------------------------------------------
if (-not (Test-Path .venv)) {
    Write-Host "Erstelle virtuelle Umgebung (.venv)..."
    & $py312 -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Installiere Abhaengigkeiten..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt

# -- models --------------------------------------------------------
$models = @(
    @{ Path = "models\hand_landmarker.task"
       Url  = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
       Name = "Hand-Landmarker" },
    @{ Path = "models\face_landmarker.task"
       Url  = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
       Name = "Face-Landmarker" }
)

New-Item -ItemType Directory -Force -Path models | Out-Null
foreach ($m in $models) {
    if (-not (Test-Path $m.Path)) {
        Write-Host "Lade $($m.Name)-Modell..."
        # Progress rendering makes Invoke-WebRequest an order of magnitude slower.
        $prev = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $m.Url -OutFile $m.Path -UseBasicParsing
        } finally {
            $ProgressPreference = $prev
        }
    }
}

# -- MSVC runtime check --------------------------------------------
# cv2_enumerate_cameras ships a compiled extension that links against the
# Visual C++ runtime.  It is NOT part of a bare Windows install, and OpenCV
# itself does not reveal the gap (its wheel bundles its own copy).  Without it
# camera enumeration falls back to probing indices one by one, which takes
# ~20 s and blows past the caller's 5 s timeout: the camera picker then just
# stays empty, with only a DLL load error deep inside a third-party import to
# explain it.  Check explicitly and say so.
$missing = @("MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll") |
    Where-Object { -not (Test-Path (Join-Path $env:WINDIR "System32\$_")) }

if ($missing) {
    Write-Host ""
    Write-Host "WARNUNG: Visual C++ Runtime fehlt ($($missing -join ', '))." -ForegroundColor Yellow
    Write-Host "  Ohne sie bleibt die Kameraauswahl leer (Enumeration laeuft in einen Timeout)."
    Write-Host "  Installieren mit:  winget install --id Microsoft.VCRedist.2015+.x64"
    Write-Host "  (benoetigt Administratorrechte)"
    Write-Host ""
} else {
    # Prove the extension actually loads - the DLLs being present is necessary
    # but not sufficient.
    & $venvPy -c "import cv2, cv2_enumerate_cameras" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "WARNUNG: cv2_enumerate_cameras laedt nicht." -ForegroundColor Yellow
        Write-Host "  Die Kameraauswahl faellt auf langsames Index-Probing zurueck."
        Write-Host "  Details:  .venv\Scripts\python.exe -c ""import cv2_enumerate_cameras"""
        Write-Host ""
    }
}

Write-Host "Sidecar-Setup abgeschlossen." -ForegroundColor Green
Write-Host "Test:  .venv\Scripts\python.exe sidecar.py --stdio"
