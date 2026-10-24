@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Node.js。请安装 Node.js 24 或更高版本后再运行。
  pause
  exit /b 1
)

for /f "delims=" %%v in ('node -p "process.versions.node.split('.')[0]"') do set "NODE_MAJOR=%%v"
if not defined NODE_MAJOR (
  echo [错误] 无法读取 Node.js 版本。
  pause
  exit /b 1
)
if %NODE_MAJOR% LSS 24 (
  echo [错误] 需要 Node.js 24 或更高版本，当前主版本为 %NODE_MAJOR%。
  echo 下载地址：https://nodejs.org/
  pause
  exit /b 1
)

node "novel-workbench\server\index.mjs" --open %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo 工作台已退出，错误码 %CODE%。
  pause
  exit /b %CODE%
)
