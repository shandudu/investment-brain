@echo off
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYTHON_EXE="
set "PYTHON_ARGS="
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined PYTHON_EXE where python >nul 2>nul && set "PYTHON_EXE=python"
if not defined PYTHON_EXE where py >nul 2>nul && set "PYTHON_EXE=py" && set "PYTHON_ARGS=-3"
if not defined PYTHON_EXE (
  echo Python 3 was not found.
  pause
  exit /b 1
)
"%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r "%APP_DIR%requirements-free.txt"
if errorlevel 1 (
  echo Free data provider installation failed.
  pause
  exit /b 1
)
echo AKShare and BaoStock are ready. Restart the application.
pause
