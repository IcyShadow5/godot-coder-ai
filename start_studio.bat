@echo off
setlocal
cd /d "%~dp0"

rem Prefer the project virtual environment; fall back to a system Python so
rem a bare checkout without .venv can still start the Studio.
if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
  set "PYTHONPATH=%~dp0src"
  echo [Godot Coder Studio] .venv not found - using system python with PYTHONPATH=src
)

rem Dependency check: fail with a clear message instead of a traceback.
"%PYTHON%" -c "import importlib.util, sys; missing=[m for m in ('fastapi','torch') if importlib.util.find_spec(m) is None]; sys.exit('missing packages: '+', '.join(missing)) if missing else None"
if errorlevel 1 (
  echo [Godot Coder Studio] Required packages are missing in the selected Python.
  echo Install them with:  .venv\Scripts\pip install -e ".[dev]"
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

"%PYTHON%" -m godot_coder.studio
