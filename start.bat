@echo off
rem Thin wrapper around start.ps1 so there is only one launcher to maintain.
rem -ExecutionPolicy Bypass lets this work on a machine whose policy is still
rem Restricted (the Windows default), without changing any system setting.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 (
    echo.
    echo Motryx exited with an error.
    pause
)
