@echo off
REM ---------------------------------------------------------------------
REM  Migration rehearsal.
REM
REM  Production is only ever READ. Everything else happens in a throwaway
REM  stack on the test box, on its own port, with its own database.
REM
REM  Run as a module from the repo root so it can import the deploy tool's
REM  SSH and password handling rather than carrying a second copy.
REM ---------------------------------------------------------------------
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found on the PATH.
  echo Install Python 3 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

python -c "import paramiko" >nul 2>&1
if errorlevel 1 (
  echo Installing paramiko ^(needed to talk to the servers^)...
  python -m pip install paramiko
)

python tools\migrate_gui.py
if errorlevel 1 pause
