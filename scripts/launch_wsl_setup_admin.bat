@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%setup_windows_wsl_admin.ps1"
set "BULKSEQ_DISTRO=%~1"
if "%BULKSEQ_DISTRO%"=="" set "BULKSEQ_DISTRO=Ubuntu"

REM The script path and distro reach PowerShell through the environment, never interpolated
REM into the -Command string: an install path containing an apostrophe (C:\Users\O'Brien\...)
REM closed the single-quoted literal and the elevated process was started with a broken
REM argument list. The path is still wrapped in double quotes so a space in it survives
REM Start-Process joining the argument array.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('\"' + $env:PS_SCRIPT + '\"'),'-Distro',('\"' + $env:BULKSEQ_DISTRO + '\"')) -Verb RunAs"

if errorlevel 1 (
    echo Failed to start Administrator PowerShell setup.
    pause
    exit /b 1
)

exit /b 0
