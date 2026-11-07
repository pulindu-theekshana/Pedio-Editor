@echo off
setlocal
cd /d "%~dp0"

if not exist requirements.txt (
    echo Put setup.bat inside the pedio-editor folder, next to main.py, then run it again.
    pause
    exit /b 1
)

echo ==============================
echo  Pedio Editor - one-time setup
echo ==============================
echo.

REM --- 1. Find a compatible Python (3.10 - 3.13) ---
set "PYCMD="
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYCMD (
        py -%%V --version >nul 2>nul
        if not errorlevel 1 set "PYCMD=py -%%V"
    )
)
if not defined PYCMD (
    python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo No Python 3.10 to 3.13 found. Installing Python 3.12 with winget...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo Python installed. CLOSE this window, open a NEW cmd and run setup.bat again.
    pause
    exit /b 1
)
echo Using: %PYCMD%
%PYCMD% --version
echo.

REM --- 2. Virtual environment ---
if not exist venv\Scripts\python.exe (
    echo Creating virtual environment...
    %PYCMD% -m venv venv
    if errorlevel 1 goto :fail
)
call venv\Scripts\activate.bat

REM --- 3. Python packages ---
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
pip install -r requirements.txt py7zr
if errorlevel 1 goto :fail

REM --- 4. libmpv (mpv-2.dll) and FFmpeg (bin\) ---
echo.
python scripts\setup_deps.py
if errorlevel 1 goto :fail

REM --- 5. Check everything ---
echo.
python scripts\check_setup.py
if errorlevel 1 goto :fail

echo.
echo Setup finished. To start the app, open cmd in this folder and run:
echo     venv\Scripts\activate
echo     python main.py
pause
exit /b 0

:fail
echo.
echo Setup failed. See the error above.
pause
exit /b 1