@echo off
setlocal
title KABi IC Events Hub - set the address for approval e-mails
cd /d "%~dp0"

echo.
echo   Approval e-mails carry a link back into the hub. That link has to use this
echo   PC's network address, or it will not open on anyone else's machine.
echo.

python set_address.py %*

echo.
pause
