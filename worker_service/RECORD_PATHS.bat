@echo off
REM Opens the mouse-path recorder. Double-click this.
REM
REM Uses the SAME Python the agent runs on where there is a virtual
REM environment beside it, so the tool sees the same packages the agent does
REM rather than whichever Python happens to be first on PATH.

setlocal
cd /d "%~dp0"

if exist "..\.venv\Scripts\python.exe" (
    set PY=..\.venv\Scripts\python.exe
) else (
    set PY=python
)

REM pynput is what follows the mouse and gives F9 a global hotkey. Installed
REM on demand rather than shipped in requirements.txt, because the agent
REM itself never needs it — only this tool does, and only on the one machine
REM the recording happens on.
%PY% -c "import pynput" 2>nul
if errorlevel 1 (
    echo Installing pynput, one moment...
    %PY% -m pip install pynput
)

%PY% path_recorder.py

if errorlevel 1 (
    echo.
    echo The recorder could not start. The message above says why.
    pause
)
endlocal
