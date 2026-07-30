@echo off
REM ===================================================================
REM  Local dev setup launcher.
REM
REM  Double-click this. Creates the virtualenv if needed, makes sure
REM  dependencies are current, then opens the setup GUI.
REM
REM  NOTE: dependencies are synced on EVERY run, not just the first.
REM  An earlier version only installed them when creating the venv, so
REM  adding a package to requirements.txt later left existing setups
REM  broken with a missing-module error. "pip install -r" is a no-op
REM  when everything is already satisfied, so the cost is a second or two.
REM ===================================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create a virtual environment.
        echo Install Python 3.11+ and make sure "python" works from a terminal.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
)

echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Scroll up for the reason.
    echo.
    echo If this persists, delete the .venv folder and run this again
    echo to rebuild the environment from scratch.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts\dev_setup.py %*

if errorlevel 1 pause
