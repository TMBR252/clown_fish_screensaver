@echo off
rem Start the clownfish wallpaper automatically when you log in.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\startup.ps1" -Install
echo.
pause
