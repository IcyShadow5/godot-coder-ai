@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Godot Coder AI] .venv was not found.
  echo Run the regular installation or the upgrade first.
  pause
  exit /b 1
)
echo.
echo Setting up Secure Remote Studio for Tailscale Serve.
echo The Studio itself stays bound to 127.0.0.1:8765.
echo.
".venv\Scripts\python.exe" -m godot_coder.remote_access --root "%CD%" configure --port 8765
if errorlevel 1 (
  echo.
  echo Setup failed. No private projects were uploaded.
  pause
  exit /b 1
)
echo.
echo Now start start_studio.bat and open the displayed Tailscale HTTPS address on your phone.
pause
