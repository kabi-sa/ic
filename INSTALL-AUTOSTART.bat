@echo off
setlocal
title KABi IC Events Hub - start automatically with Windows
cd /d "%~dp0"

rem  Registers a scheduled task so the hub comes back by itself after a restart,
rem  keeps running when nobody is signed in, and restarts if it ever stops.
rem  Must be run as administrator: right-click, "Run as administrator".

net session >nul 2>nul
if errorlevel 1 (
  echo.
  echo   This needs administrator rights.
  echo   Right-click INSTALL-AUTOSTART.bat and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

set PORT=8080
if not "%~1"=="" set PORT=%~1
set TASKNAME=KABi IC Events Hub

for /f "delims=" %%p in ('where python') do set PYEXE=%%p& goto :found
:found
if not defined PYEXE (
  echo.
  echo   Python was not found. Install it first, then run this again.
  echo.
  pause
  exit /b 1
)

echo.
echo   Registering "%TASKNAME%"
echo     python : %PYEXE%
echo     folder : %CD%
echo     port   : %PORT%
echo.

rem  SYSTEM so it runs with no one logged in; /RL HIGHEST so the port bind is never refused.
schtasks /Create /TN "%TASKNAME%" /SC ONSTART /RU SYSTEM /RL HIGHEST /F ^
  /TR "cmd /c cd /d \"%CD%\" && \"%PYEXE%\" server.py %PORT%" >nul
if errorlevel 1 (
  echo   Could not register the task. Nothing was changed.
  echo.
  pause
  exit /b 1
)

rem  Bring it back if it ever exits, and never stop it for running too long.
schtasks /Change /TN "%TASKNAME%" /RI 1 /DU 9999:59 >nul 2>nul

echo   Done. The hub will start by itself whenever this PC boots.
echo.
echo   Start it now without rebooting:
echo       schtasks /Run /TN "%TASKNAME%"
echo   Stop it:
echo       schtasks /End /TN "%TASKNAME%"
echo   Remove the automatic start again:
echo       run UNINSTALL-AUTOSTART.bat
echo.
set /p RUNNOW=  Start the hub now? (y/n):
if /i "%RUNNOW%"=="y" (
  schtasks /Run /TN "%TASKNAME%" >nul
  echo.
  echo   Started. Give it a few seconds, then open http://localhost:%PORT%
)
echo.
pause
