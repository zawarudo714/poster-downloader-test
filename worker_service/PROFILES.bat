@echo off
REM Opens the profile picker. Double-click this.
REM
REM Uses the SAME Python the agent runs on where there is a virtual
REM environment beside it, so the tool sees the same packages the agent does
REM rather than whichever Python happens to be first on PATH.

setlocal
cd /d "%~dp0"

if exist "..\.venv\Scripts\python.exe" (
    "..\.venv\Scripts\python.exe" profile_launcher.py
) else (
    python profile_launcher.py
)

if errorlevel 1 (
    echo.
    echo The picker could not start. The message above says why.
    pause
)
endlocal
