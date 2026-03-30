@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
set PATH=%cd%\leapc_cffi;%PATH%
python main.py %*
