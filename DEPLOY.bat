@echo off
REM ===================================================================
REM  Deploy tool launcher.
REM
REM  Double-click this. It makes sure paramiko is present (the only
REM  dependency the tool has that Python does not ship with) and then
REM  opens the window.
REM ===================================================================

cd /d "%~dp0"

python -c "import paramiko" 2>nul
if errorlevel 1 (
    echo Installing paramiko, one moment...
    python -m pip install paramiko
    if errorlevel 1 (
        echo.
        echo Could not install paramiko. Is Python on your PATH?
        pause
        exit /b 1
    )
)

python "tools\deploy_gui.py"

REM Only pauses if something went wrong, so a normal close does not
REM leave a black window sitting there.
if errorlevel 1 pause
