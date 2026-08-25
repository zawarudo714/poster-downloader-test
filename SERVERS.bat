@echo off
REM ---------------------------------------------------------------------
REM  Server launcher — one click to a logged-in terminal.
REM
REM  Installs an SSH key the first time, using the password already saved
REM  by the deploy and migration tools. After that no password is involved
REM  at all, and `ssh` opens in a normal Windows terminal with colours,
REM  scrollback and everything else a shell needs.
REM ---------------------------------------------------------------------
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found on the PATH.
  echo Install Python 3 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

where ssh >nul 2>&1
if errorlevel 1 (
  echo Windows OpenSSH was not found.
  echo Settings ^> Apps ^> Optional features ^> Add ^> OpenSSH Client
  pause
  exit /b 1
)

python -c "import paramiko" >nul 2>&1
if errorlevel 1 (
  echo Installing paramiko ^(needed once, to install the key^)...
  python -m pip install paramiko
)

python tools\servers_gui.py
if errorlevel 1 pause
