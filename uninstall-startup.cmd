@echo off
rem Stop the clownfish wallpaper starting at login.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\startup.ps1" -Remove
echo.
pause
