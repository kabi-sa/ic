@echo off
setlocal
title KABi IC Events Hub - import recaps to the live site
cd /d "%~dp0"

echo.
echo   ==========================================================
echo     Import the monthly recaps and quarterly updates
echo     into the LIVE (hosted) hub
echo   ==========================================================
echo.
echo     13 monthly recaps  (Jul 2025 - Jul 2026)
echo      3 quarterly updates (Q3 2025, Q4 2025, Q1 2026)
echo.
echo     Each one is filed on the last Thursday of its period,
echo     signed off by Mashael and Sahabah, with its published
echo     sheet attached.
echo.
echo     The sheets are 4-7 MB PNGs. The hosted site accepts
echo     3.4 MB per file, so each is re-encoded as a JPEG at
echo     FULL resolution on the way - about 1.4 MB, no loss of
echo     detail. Your original files are not touched.
echo.
echo     Safe to run twice: an entry already there is topped up
echo     with anything missing rather than duplicated.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   Python was not found on this machine.
  echo.
  pause
  exit /b 1
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo   This needs Pillow to re-encode the sheets. Install it with:
  echo.
  echo       pip install Pillow
  echo.
  pause
  exit /b 1
)

set "ICSITE=https://ic-beta.vercel.app"
set /p "ICSITE=  Live site [%ICSITE%]: "
echo.
echo   Sign in as the Internal Communication administrator.
echo   What you type is visible on screen but is NOT saved anywhere -
echo   it is used for this run only.
echo.
set /p "IC_ADMIN_PASSWORD=  Password for ic@kabi.ai: "
echo.

if "%IC_ADMIN_PASSWORD%"=="" (
  echo   Nothing entered - stopping.
  echo.
  pause
  exit /b 1
)

echo   Working. Sixteen entries with a sheet each, so give it a few minutes.
echo.
python recap_import.py "%ICSITE%"
set "IC_ADMIN_PASSWORD="

echo.
echo   ----------------------------------------------------------
echo     Done. Open the live site, go to History, and filter by
echo     Monthly recaps / Quarterly updates to see them.
echo.
echo     Tip: type  cls  and press Enter to clear this window.
echo   ----------------------------------------------------------
echo.
pause
