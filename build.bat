@echo off
REM Build "dist\Pedio Editor.exe" (setup.bat must have been run once, so bin\ffmpeg.exe and the DLLs exist)
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Run setup.bat first.
  exit /b 1
)
call venv\Scripts\activate
if not exist assets\icon.ico python scripts\make_icon.py
python -m PyInstaller --noconfirm --clean Pedio.spec
if errorlevel 1 exit /b 1
echo.
echo Built: dist\Pedio Editor.exe
pause
