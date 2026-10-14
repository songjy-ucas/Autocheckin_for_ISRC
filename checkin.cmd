@echo off
setlocal
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%~dp0checkin.py" %*
) else (
  python "%~dp0checkin.py" %*
)
exit /b %errorlevel%
