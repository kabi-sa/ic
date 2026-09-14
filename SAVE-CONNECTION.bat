@echo off
setlocal
title KABi IC Events Hub - save connection string
cd /d "%~dp0"

echo.
echo   Save the Supabase connection string to a local .env file.
echo   The file stays on this PC and is never committed to Git.
echo.
set /p ICURL=  Connection string: 
echo.
if "%ICURL%"=="" (
  echo   Nothing entered - no changes made.
  echo.
  pause
  exit /b 1
)
> ".env" echo IC_DATABASE_URL=%ICURL%
echo   Saved to .env
echo.
echo   From now on VERIFY-SUPABASE.bat and START.bat use the hosted
echo   database. Delete .env to go back to the local file.
echo.
pause
