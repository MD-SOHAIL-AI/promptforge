@echo off
setlocal EnableExtensions DisableDelayedExpansion
title ForgeX - Install Dependencies

rem Resolve the project from this file, even when double-clicked elsewhere.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-dependencies.ps1"
set "FORGEX_INSTALL_EXIT=%ERRORLEVEL%"

echo.
if "%FORGEX_INSTALL_EXIT%"=="0" (
    echo Installation finished successfully.
) else (
    echo Installation failed. Read the error and installation log shown above.
)
echo.
pause
exit /b %FORGEX_INSTALL_EXIT%
