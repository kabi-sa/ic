@echo off
setlocal enabledelayedexpansion
title KABi - IC Events Approval Hub (hosting for the team)
cd /d "%~dp0"

rem  Runs the hub so other people on the KABi network can reach it, rather than
rem  only this machine. The server already listens on every network interface;
rem  what matters is that colleagues use this PC's address, not "localhost".

set PORT=8080
if not "%~1"=="" set PORT=%~1

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python was not found on this machine.
  echo   Install Python 3.10 or newer from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

rem  Work out this PC's address on the network, for the message below.
set LANIP=
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
  if not defined LANIP set LANIP=%%a
)
set LANIP=%LANIP: =%

echo.
echo   ==========================================================
echo     KABi IC Events Approval Hub
echo   ==========================================================
echo.
echo     This window must stay open while the hub is in use.
echo     Closing it stops the hub for everyone.
echo.
if defined LANIP (
  echo     Colleagues open:   http://%LANIP%:%PORT%
) else (
  echo     Colleagues open:   http://^<this-pc-name^>:%PORT%
)
echo     On this PC:        http://localhost:%PORT%
echo.
echo     If nobody else can reach it, run OPEN-FIREWALL.bat once
echo     as administrator.
echo.
echo   ----------------------------------------------------------
echo.

python server.py %PORT%

echo.
echo   The hub has stopped.
pause
