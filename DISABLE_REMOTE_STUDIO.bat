@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -m godot_coder.remote_access --root "%CD%" disable --reset-serve
if errorlevel 1 pause
