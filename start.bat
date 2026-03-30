@echo off
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\pip install -r requirements.txt
)
call .venv\Scripts\activate.bat

rem Copy leapc_cffi from Ultraleap SDK if not present locally
if not exist leapc_cffi (
    if exist "C:\Program Files\Ultraleap\LeapSDK\leapc_cffi" (
        echo Copying LeapC bindings from SDK...
        xcopy /E /I "C:\Program Files\Ultraleap\LeapSDK\leapc_cffi" leapc_cffi >nul
    ) else (
        echo WARNING: leapc_cffi not found. Install Ultraleap Tracking from:
        echo   https://www.ultraleap.com/downloads/leap-controller/
    )
)

rem Rename .pyd for current Python version if needed
if exist leapc_cffi (
    for /f %%v in ('python -c "import sys; print(f\"cp{sys.version_info.major}{sys.version_info.minor}\")"') do set PYVER=%%v
    if not exist "leapc_cffi\_leapc_cffi.%PYVER%-win_amd64.pyd" (
        for %%f in (leapc_cffi\_leapc_cffi.cp*-win_amd64.pyd) do (
            echo Copying %%~nxf -^> _leapc_cffi.%PYVER%-win_amd64.pyd
            copy "%%f" "leapc_cffi\_leapc_cffi.%PYVER%-win_amd64.pyd" >nul
            goto :pyd_done
        )
        :pyd_done
    )
)

set PATH=%cd%\leapc_cffi;%PATH%
python main.py %*
