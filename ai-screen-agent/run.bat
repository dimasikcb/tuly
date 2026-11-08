@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Screen Bridge (Computer Use)

set "PY="
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

echo.
echo [!] Python не найден.
echo Установите Python 3.10+ и включите опцию "Add python.exe to PATH".
echo.
pause
exit /b 1

:run
%PY% screen_agent.py
if errorlevel 1 (
    echo.
    echo [!] Программа завершилась с ошибкой.
    pause
)
