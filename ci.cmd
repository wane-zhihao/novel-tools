@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=%WORKBENCH_PYTHON%"
if "%PY%"=="" set "PY=python"

REM engines 要求 Node >=24：低于 24 会在 api.test.mjs 撞 fs.cpSync 段错误。
for /f "tokens=1 delims=." %%a in ('node -p "process.versions.node"') do set "NODE_MAJOR=%%a"
if "%NODE_MAJOR%"=="" set "NODE_MAJOR=0"
if %NODE_MAJOR% LSS 24 (
  echo ERROR: 需要 Node ^>=24，本机可用 C:\Users\Wane-\AppData\Local\Programs\nodejs\node-v24.16.0-win-x64\node.exe 1>&2
  exit /b 1
)

for /r "novel-workbench\server" %%f in (*.mjs) do (
  node --check "%%f" || exit /b 1
)
"%PY%" -m compileall -q novel-workbench\scripts novel-workbench\server\tests\python_contracts.py || exit /b 1
call npm test --prefix novel-workbench
exit /b %ERRORLEVEL%
