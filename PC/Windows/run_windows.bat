@echo off
setlocal

set "ROOT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT_DIR%run_windows.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Crystal Client exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
