@echo off
REM ---------------------------------------------------------------------
REM  FineArtAmerica listing check — measuring tool, runs on the laptop.
REM
REM  Standard library only, so there is nothing to install and no virtual
REM  environment to keep in step. Run as a MODULE from the repo root, the
REM  same as RECORD_PATHS.bat, so it can read app/pipeline.py for the real
REM  title normaliser instead of carrying its own copy.
REM ---------------------------------------------------------------------
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found on the PATH.
  echo Install Python 3 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

python tools\faa_url_check.py
if errorlevel 1 pause
