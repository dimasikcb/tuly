@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "SELF=%~f0"

rem Elevate via UAC if not admin
powershell -NoProfile -Command "$id=[Security.Principal.WindowsIdentity]::GetCurrent(); $p=New-Object Security.Principal.WindowsPrincipal($id); if(-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){ Start-Process -FilePath $env:SELF -Verb RunAs; exit 1 } else { exit 0 }"
if errorlevel 1 exit /b

rem Already elevated: run tools.pyw without console window
set "PYW="
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW for /f "delims=" %%P in ('where pyw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW (
    echo [!] Python not found.
    pause
    exit /b 1
)
start "" "%PYW%" "%~dp0tools.pyw" %*
