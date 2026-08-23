@echo off
REM Opens the mouse-path recorder. Double-click this.
REM
REM Run as a MODULE from the parent folder (-m worker_service.path_recorder),
REM not as a loose script. uploader.py imports its sibling with `from .client
REM import ...`, and a relative import only resolves when Python knows the
REM package — running `python path_recorder.py` dies on that line before the
REM window ever appears.

setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

REM pynput is what follows the mouse and gives F9 a hotkey that works while
REM Chrome is in front. Installed on demand rather than shipped in
REM requirements.txt, because the agent never needs it — only this tool does,
REM and only on the machine the recording happens on.
%PY% -c "import pynput" 2>nul
if errorlevel 1 (
    echo Installing pynput, one moment...
    %PY% -m pip install pynput
)

%PY% -m worker_service.path_recorder

if errorlevel 1 (
    echo.
    echo The recorder could not start. The message above says why.
    pause
)
endlocal
