@echo off
setlocal
cd /d "%~dp0"

rem --- One click launcher for Windows 11. First run prepares .venv, later runs are console-free. ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3 not found. Install it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\.diary-ready" (
  echo First run only: preparing environment. This takes a few minutes...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
  call ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
  call ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 goto fail
  echo ready> ".venv\.diary-ready"
  echo Environment ready.
)

if not exist ".venv\Scripts\pythonw.exe" (
  echo ERROR: .venv not ready — run the setup first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "app.pyw" %*
exit /b 0

:fail
echo Setup failed. Read the messages above, then run run.bat again.
pause
exit /b 1
