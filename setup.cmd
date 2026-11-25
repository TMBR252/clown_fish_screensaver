@echo off
rem One-step setup: build the virtual environment and check this machine.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1"
echo.
pause
