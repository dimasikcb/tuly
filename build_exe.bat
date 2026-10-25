@echo off
setlocal
cd /d "%~dp0"

echo.
echo  === Build Tools.exe ===
echo.

where python >nul 2>&1
if errorlevel 1 goto no_python

if not exist "tools.pyw" goto no_source

python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo  Installing PyInstaller...
    python -m pip install --upgrade pyinstaller
    if errorlevel 1 goto no_pyinstaller
)

echo  Building. This can take a few minutes...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Tools --distpath . --workpath build --specpath build --exclude-module matplotlib --exclude-module pandas --exclude-module scipy --exclude-module PyQt5 --exclude-module PySide6 tools.pyw
if errorlevel 1 goto failed

echo.
echo  Done: Tools.exe
echo  Settings and templates stay next to the exe.
echo  Edit tools.pyw and run this file again to rebuild.
echo.
pause
exit /b 0

:no_python
echo.
echo  Python not found in PATH. Install Python 3.10+ and try again.
pause
exit /b 1

:no_source
echo.
echo  tools.pyw not found next to this file.
pause
exit /b 1

:no_pyinstaller
echo.
echo  Could not install PyInstaller.
pause
exit /b 1

:failed
echo.
echo  Build failed. See the messages above.
pause
exit /b 1
