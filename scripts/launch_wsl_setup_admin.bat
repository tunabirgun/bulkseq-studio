@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%setup_windows_wsl_admin.ps1"
set "DISTRO=%~1"
if "%DISTRO%"=="" set "DISTRO=Ubuntu"

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','\"%PS_SCRIPT%\"','-Distro','%DISTRO%') -Verb RunAs"

if errorlevel 1 (
    echo Failed to start Administrator PowerShell setup.
    pause
    exit /b 1
)

exit /b 0
