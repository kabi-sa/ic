@echo off
setlocal
title KABi IC Events Hub - push to GitHub
cd /d "%~dp0"

echo.
echo   ============================================================
echo     Push the IC Events Hub to GitHub
echo     repo: https://github.com/Njoodao/IC.git
echo   ============================================================
echo.
echo   26 files, about 550 KB.
echo   The database, uploaded files and any .env are NOT included.
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo   Git was not found. Install it from https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)

echo   Files that will be pushed:
echo.
git ls-files
echo.
echo   ------------------------------------------------------------
echo   A browser or dialog may open asking you to sign in to GitHub.
echo   ------------------------------------------------------------
echo.
pause

git push -u origin main
echo.
if errorlevel 1 (
  echo   Push failed - see the message above.
  echo   Most common cause: not signed in to GitHub on this PC.
) else (
  echo   Done. Now go to vercel.com, import Njoodao/IC,
  echo   add the IC_DATABASE_URL environment variable, and Deploy.
)
echo.
pause
