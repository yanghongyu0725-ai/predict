@echo off
setlocal

set BRANCH=%1
if "%BRANCH%"=="" set BRANCH=main

echo [INFO] 强制同步到 origin/%BRANCH% （会丢弃本地未提交修改）

git fetch origin
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set TS=%%i
set SNAPSHOT=backup-before-force-sync-%TS%
git branch %SNAPSHOT% >nul 2>nul
echo [INFO] 已创建本地快照分支: %SNAPSHOT%

git checkout %BRANCH%
git reset --hard origin/%BRANCH%
git clean -fd

echo [OK] 已同步到 origin/%BRANCH%
endlocal
