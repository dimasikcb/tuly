@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 2GIS leads
set "LEADS_BAT=1"

set "PY="
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

echo.
echo Python not found.
echo Install from python.org and enable "Add python.exe to PATH".
echo.
pause
exit /b 1

:run
%PY% "%~dp0leads2gis.py" %*
echo.
pause
