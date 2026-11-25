@echo off
rem Start one tank per screen, detached, with no console window.
rem Pass options straight through, e.g.  start.cmd --fps 20 --no-gpu
setlocal
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo The virtual environment is missing. Run setup.cmd first.
    pause
    exit /b 1
)
start "clownfish" /b "%PYW%" "%~dp0run.pyw" %*
