@echo off
setlocal
cd /d "%~dp0"

rem --- find a real Python (avoid the Microsoft Store stub) ---
set "PYEXE="
for %%V in (314 313 312 311 310) do (
  if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
)
if not defined PYEXE if exist "C:\Python312\python.exe" set "PYEXE=C:\Python312\python.exe"
if not defined PYEXE (
  where py >nul 2>nul && for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE set "PYEXE=python"

rem --- virtual environment ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment ...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 goto :err
)

rem --- dependencies ---
".venv\Scripts\python.exe" -c "import numpy, PIL, mss, chess" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 goto :err
)

rem --- run ---
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
goto :eof

:err
echo.
echo Setup failed. Install Python 3.10+ from https://www.python.org/downloads/ and run this again.
pause
