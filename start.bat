@echo off
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
if exist "%USERPROFILE%\.codex\plugins\.plugin-appserver\codex.exe" set "CODEX_EXECUTABLE=%USERPROFILE%\.codex\plugins\.plugin-appserver\codex.exe"
if not exist "%APP_DIR%node_modules" (
  echo Missing local application dependencies. Please run the setup again.
  pause
  exit /b 1
)
set "PYTHON_EXE="
set "PYTHON_ARGS="
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined PYTHON_EXE where python >nul 2>nul && set "PYTHON_EXE=python"
if not defined PYTHON_EXE where py >nul 2>nul && set "PYTHON_EXE=py" && set "PYTHON_ARGS=-3"
if not defined PYTHON_EXE (
  echo Python 3 was not found. Install Python 3 or open this project from Codex.
  pause
  exit /b 1
)
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('akshare') or importlib.util.find_spec('baostock') else 1)"
if errorlevel 1 echo Warning: free A-share providers are not installed. Run setup-free-data.bat once.
set "PNPM_EXE="
where pnpm >nul 2>nul && set "PNPM_EXE=pnpm"
if not defined PNPM_EXE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" set "PNPM_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"
if not defined PNPM_EXE (
  echo pnpm was not found. Install pnpm or open this project from Codex.
  pause
  exit /b 1
)
set "pnpm_config_verify_deps_before_run=false"
set "PYTHONPATH=%APP_DIR%"
start "Investment Brain Backend" /min "%PYTHON_EXE%" %PYTHON_ARGS% -m backend.app --no-browser
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:3000'"
rem 本地启动使用 vinext dev，以加载 Worker 的 /api 代理到 127.0.0.1:8765。
call "%PNPM_EXE%" run dev -- --host 127.0.0.1
