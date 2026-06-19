@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" call :try_python ".venv\Scripts\python.exe"
if "%BULKSEQ_LAUNCHED%"=="1" goto :done

where python >nul 2>nul
if %errorlevel%==0 call :try_python "python"
if "%BULKSEQ_LAUNCHED%"=="1" goto :done

where py >nul 2>nul
if %errorlevel%==0 call :try_python "py" "-3"
if "%BULKSEQ_LAUNCHED%"=="1" goto :done

if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" call :try_python "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if "%BULKSEQ_LAUNCHED%"=="1" goto :done

echo.
echo BulkSeq Studio could not find a working Python interpreter.
echo.
echo Create a virtual environment and install dependencies:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1

:try_python
set "PY_CMD=%~1"
set "PY_ARG=%~2"
if "%PY_ARG%"=="" (
    %PY_CMD% -c "import sys" >nul 2>nul
) else (
    %PY_CMD% %PY_ARG% -c "import sys" >nul 2>nul
)
if errorlevel 1 exit /b 0

set "BULKSEQ_LAUNCHED=1"
if "%PY_ARG%"=="" (
    %PY_CMD% -m app.main
) else (
    %PY_CMD% %PY_ARG% -m app.main
)
set "BULKSEQ_EXIT=%errorlevel%"
exit /b 0

:done
if not "%BULKSEQ_EXIT%"=="0" (
    echo.
    echo BulkSeq Studio exited with an error.
    pause
)
exit /b %BULKSEQ_EXIT%
