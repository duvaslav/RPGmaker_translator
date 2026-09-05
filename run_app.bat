@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found: .venv
    echo Restore it with: py -3.12 -m venv .venv
    pause
    exit /b 1
)

start "" "%PYTHON_EXE%" "%~dp0run.py"
exit /b 0
