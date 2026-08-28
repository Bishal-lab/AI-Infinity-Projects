@echo off
REM Import whatever is in inbox\ and write a populated dashboard to out\.
REM Double-click this file, or call it from Task Scheduler.
cd /d "%~dp0"
python import_exports.py %*
if errorlevel 1 (
  echo.
  echo Import did not complete. Read the message above.
)
echo.
pause
