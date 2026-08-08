@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Godot Coder Studio] .venv was not found.
  echo Create the virtual environment first and install the project.
  pause
  exit /b 1
)

rem Single instance: refuse to start a second Studio on the same port.
netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [Godot Coder Studio] A Studio instance already appears to be running on port 8765.
  echo Close the other Studio window first, or start it with a different --port.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m godot_coder.studio
if errorlevel 1 pause
