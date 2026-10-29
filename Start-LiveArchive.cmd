@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0tools\start-live-archive.ps1" -WithFrontend
pause
