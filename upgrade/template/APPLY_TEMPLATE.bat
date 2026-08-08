@echo off
setlocal enabledelayedexpansion

echo ========================================
echo  Godot Coder AI vX.Y.Z - Upgrade
echo ========================================
echo.

set /p PROJECT="Path to your godot-coder-ai project: "

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_VX_Y_Z_UPGRADE.ps1" -ExistingProject "%PROJECT%"

if %ERRORLEVEL% neq 0 (
    echo.
    echo Upgrade FAILED. See output above for details.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Done. You can close this window.
pause
