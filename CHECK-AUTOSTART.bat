@echo off
title KABi IC Events Hub - check the automatic start
cd /d "%~dp0"

rem  Says whether the scheduled task exists and whether the hub is running, and
rem  starts it if it is not. Deliberately asks no questions: the first version
rem  prompted for y/n inside an if-block, where cmd.exe expands the answer before
rem  you have typed it, so answering did nothing and the window looked frozen.

set TASKNAME=KABi IC Events Hub

net session >nul 2>nul
if errorlevel 1 goto notadmin

echo.
echo   ==========================================================
echo     KABi IC Events Hub - automatic start
echo   ==========================================================
echo.

schtasks /Query /TN "%TASKNAME%" >nul 2>nul
if errorlevel 1 goto notinstalled

echo   [1/2] Automatic start: INSTALLED
echo.
schtasks /Query /TN "%TASKNAME%" /V /FO LIST 2>nul | findstr /C:"Status" /C:"Last Run Time" /C:"Last Result" /C:"Run As User" /C:"Task To Run"
echo.
goto checkport

:notinstalled
echo   [1/2] Automatic start: NOT INSTALLED
echo.
echo         There is no task called "%TASKNAME%".
echo         The hub will NOT come back by itself after a restart.
echo         Run INSTALL-AUTOSTART.bat as administrator to set it up.
echo.
goto checkport

:checkport
netstat -ano | findstr /R /C:":8080 .*LISTENING" >nul
if errorlevel 1 goto notrunning
echo   [2/2] The hub IS running on port 8080.
goto done

:notrunning
echo   [2/2] The hub is NOT running. Starting it...
echo.
schtasks /Run /TN "%TASKNAME%" >nul 2>nul
if not errorlevel 1 goto started
rem  No task, or it refused: fall back to starting it directly.
start "KABi IC Events Hub" /min cmd /c "cd /d "%~dp0" && python server.py 8080"

:started
ping -n 7 127.0.0.1 >nul
netstat -ano | findstr /R /C:":8080 .*LISTENING" >nul
if errorlevel 1 echo         Still not listening. Open START.bat and read the error it prints.
if not errorlevel 1 echo         Started. Open http://localhost:8080
goto done

:done
echo.
echo   ----------------------------------------------------------
echo     schtasks /Run /TN "%TASKNAME%"     start it
echo     schtasks /End /TN "%TASKNAME%"     stop it
echo     UNINSTALL-AUTOSTART.bat            remove the automatic start
echo   ----------------------------------------------------------
echo.
pause
exit /b 0

:notadmin
echo.
echo   This needs administrator rights: a task that runs as SYSTEM is not
echo   visible from an ordinary window.
echo.
echo   Close this, then right-click CHECK-AUTOSTART.bat and choose
echo   "Run as administrator".
echo.
pause
exit /b 1
