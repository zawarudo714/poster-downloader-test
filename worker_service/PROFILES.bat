@echo off
REM Opens the profile picker. Double-click this.
REM
REM Run as a MODULE from the parent folder, not as a loose script. The picker
REM imports `profiles_root` from uploader.py so there is exactly ONE
REM definition of where profiles live — and uploader.py uses a relative
REM import, which only resolves when Python knows the package. Run loose, that
REM import quietly fails and the picker falls back to its own copy of the
REM rule: two definitions of one fact, which is how the launcher and the
REM orphan sweeper drifted apart and left the sweeper doing nothing for
REM months.

setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m worker_service.profile_launcher
) else (
    python -m worker_service.profile_launcher
)

if errorlevel 1 (
    echo.
    echo The picker could not start. The message above says why.
    pause
)
endlocal
