@echo off
rem Report what this machine can do, and what the tank found.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo The virtual environment is missing. Run setup.cmd first.
    pause
    exit /b 1
)
"%PY%" "%~dp0tools\selfcheck.py"
echo.
pause
