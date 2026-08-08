@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Error] .venv was not found.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m godot_coder.doctor
set doctor_error=%errorlevel%
".venv\Scripts\python.exe" -m pytest -q
set test_error=%errorlevel%
if not "%doctor_error%"=="0" echo Doctor reported a runtime problem.
if not "%test_error%"=="0" echo Tests failed.
if "%doctor_error%"=="0" if "%test_error%"=="0" echo Installation fully checked.
pause
