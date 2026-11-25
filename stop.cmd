@echo off
rem Stop every running tank. Ctrl+Alt+Shift+F does the same thing.
rem (wmic is being removed from Windows, so ask PowerShell instead.)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*run.pyw*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo stopped.
