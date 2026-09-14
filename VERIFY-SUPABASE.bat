@echo off
setlocal
title KABi IC Events Hub - verify Supabase
cd /d "%~dp0"

echo.
echo   ============================================================
echo     Verify the Supabase (hosted) database
echo   ============================================================
echo.

if exist ".env" (
  echo   Found a .env file - using the connection string from it.
  echo.
  goto run
)

echo   Paste your Supabase connection string below, then press Enter.
echo.
echo   Where to get it:  Supabase - HC HUB project - Connect button
echo                     - Transaction pooler tab - copy the URI
echo.
echo   It should contain "pooler" and end with :6543/postgres
echo   Replace [YOUR-PASSWORD] with the real database password.
echo.
set /p ICURL=  Connection string: 
echo.

if "%ICURL%"=="" (
  echo   Nothing entered. Run this again when you have the string.
  echo.
  pause
  exit /b 1
)

set "IC_DATABASE_URL=%ICURL%"

:run
where python >nul 2>nul
if errorlevel 1 (
  echo   Python was not found. Install it from python.org and tick
  echo   "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

python -m pip install --quiet "psycopg[binary]" >nul 2>nul

echo   Running checks...
echo.
python selftest.py
echo.
echo   ------------------------------------------------------------
echo   Screenshot the results above and send them over.
echo   Nothing was saved: the connection string disappears when you
echo   close this window (unless you put it in a .env file).
echo   ------------------------------------------------------------
echo.
pause
