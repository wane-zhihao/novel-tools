@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=%WORKBENCH_PYTHON%"
if "%PY%"=="" set "PY=python"

for /r "novel-workbench\server" %%f in (*.mjs) do (
  node --check "%%f" || exit /b 1
)
"%PY%" -m compileall -q novel-workbench\scripts novel-workbench\server\tests\python_contracts.py || exit /b 1
call npm test --prefix novel-workbench
exit /b %ERRORLEVEL%
