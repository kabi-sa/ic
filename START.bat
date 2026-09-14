@echo off
title KABi - IC Events Approval Hub
cd /d "%~dp0"

echo.
echo   Starting the KABi IC Events Approval Hub...
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   Python was not found on this machine.
  echo   Install Python 3.10 or newer from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

start "" http://localhost:8080
python server.py 8080

echo.
echo   The server has stopped.
pause
