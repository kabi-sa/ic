@echo off
setlocal
title KABi IC Events Hub - stop starting automatically
cd /d "%~dp0"

net session >nul 2>nul
if errorlevel 1 (
  echo.
  echo   This needs administrator rights.
  echo   Right-click UNINSTALL-AUTOSTART.bat and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

schtasks /End    /TN "KABi IC Events Hub" >nul 2>nul
schtasks /Delete /TN "KABi IC Events Hub" /F >nul 2>nul
echo.
echo   The hub will no longer start with Windows.
echo   Your data in data\ic_hub.db and uploads\ is untouched.
echo.
pause
