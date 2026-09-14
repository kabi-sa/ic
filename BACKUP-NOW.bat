@echo off
setlocal
title KABi IC Events Hub - back up the data
cd /d "%~dp0"

rem  Everything the hub knows lives in two places: data\ic_hub.db and uploads\.
rem  Copy both and you can restore the whole hub onto any PC.
rem
rem  The backup goes into OneDrive on purpose, even though the hub itself no longer
rem  runs from there. The hub has to stay outside OneDrive -- a sync client has no
rem  business touching a live SQLite file or a .git directory -- but a backup that
rem  exists only on the machine it protects is not really a backup, and a
rem  written-once folder is exactly what a sync client is good at.
rem
rem  Set IC_BACKUP_ROOT to send them somewhere else: a network share, a USB drive.

set "BACKUP_ROOT=%IC_BACKUP_ROOT%"
if not defined BACKUP_ROOT if defined OneDriveCommercial set "BACKUP_ROOT=%OneDriveCommercial%\Desktop\IC\ic-hub-backups"
if not defined BACKUP_ROOT if defined OneDrive set "BACKUP_ROOT=%OneDrive%\Desktop\IC\ic-hub-backups"
if not defined BACKUP_ROOT set "BACKUP_ROOT=%~dp0backups"

rem  %DATE% is formatted differently on every Windows locale, so ask Python instead.
for /f %%d in ('python -c "import datetime;print(datetime.datetime.now().strftime('%%Y%%m%%d-%%H%%M'))"') do set STAMP=%%d
if not defined STAMP set STAMP=backup
set "DEST=%BACKUP_ROOT%\%STAMP%"

if not exist "data\ic_hub.db" (
  echo.
  echo   No database found at data\ic_hub.db - nothing to back up yet.
  echo.
  pause
  exit /b 1
)

mkdir "%DEST%" 2>nul
mkdir "%DEST%\uploads" 2>nul

rem  A copy of a live SQLite file can catch it mid-write, so use its own backup
rem  command when possible and fall back to a plain copy if sqlite3 is unavailable.
python -c "import sqlite3,sys; s=sqlite3.connect('data/ic_hub.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close(); s.close()" "%DEST%\ic_hub.db"
if errorlevel 1 copy /y "data\ic_hub.db" "%DEST%\ic_hub.db" >nul

if exist "uploads\*" xcopy /e /i /q /y "uploads" "%DEST%\uploads" >nul

echo.
echo   Backed up to %DEST%
for /f %%s in ('dir /b "%DEST%\uploads" 2^>nul ^| find /c /v ""') do echo   Files copied: %%s
echo.
echo   This folder is inside OneDrive, so it syncs off this PC by itself.
echo   A backup that lives only on the machine it protects is not a backup.
echo.
pause
