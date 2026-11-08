@echo off
chcp 65001 >nul
title Страж выключения ПК
python "%~dp0shutdown_watcher.py" %*
pause
