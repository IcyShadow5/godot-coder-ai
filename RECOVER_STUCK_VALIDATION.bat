@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0RECOVER_STUCK_VALIDATION.ps1" -InstallPath "%CD%"
set "code=%ERRORLEVEL%"
pause
exit /b %code%
