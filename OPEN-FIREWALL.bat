@echo off
setlocal
title KABi IC Events Hub - allow the team through the firewall
cd /d "%~dp0"

rem  Windows blocks incoming connections by default, so colleagues cannot reach the
rem  hub until this port is allowed. Run once, as administrator.

net session >nul 2>nul
if errorlevel 1 (
  echo.
  echo   This needs administrator rights.
  echo   Right-click OPEN-FIREWALL.bat and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

set PORT=8080
if not "%~1"=="" set PORT=%~1

netsh advfirewall firewall delete rule name="KABi IC Events Hub" >nul 2>nul
netsh advfirewall firewall add rule name="KABi IC Events Hub" ^
  dir=in action=allow protocol=TCP localport=%PORT% profile=domain,private >nul
if errorlevel 1 (
  echo   Could not add the firewall rule.
  echo.
  pause
  exit /b 1
)

echo.
echo   Port %PORT% is now open to the office network.
echo   Deliberately not opened on "public" networks, so the hub stays
echo   unreachable when this laptop is on a cafe or hotel connection.
echo.
pause
