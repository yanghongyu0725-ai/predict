@echo off
setlocal

set LOG_DIR=runtime
set DEPLOY_LOG=%LOG_DIR%\one_click_deploy.log
set UI_LOG=%LOG_DIR%\ui_server.log
set UI_DEBUG_LOG=%LOG_DIR%\ui_app_debug.log
set LIVE_LOG=%LOG_DIR%\ui_live.log
set UI_PORT=8501

if not exist %LOG_DIR% mkdir %LOG_DIR%

echo ===== UI Diagnose (%date% %time%) =====
echo [1] Python 版本
python --version 2>&1

echo.
echo [2] 关键依赖检查
python scripts/check_env.py 2>&1

echo.
echo [3] 8501端口监听状态
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %UI_PORT% -ErrorAction SilentlyContinue | Format-Table -AutoSize LocalAddress,LocalPort,State,OwningProcess" 2>nul
if errorlevel 1 (
  echo (Get-NetTCPConnection不可用，尝试netstat)
)
netstat -ano | findstr :%UI_PORT%

echo.
echo [4] HTTP探测
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%UI_PORT%' -TimeoutSec 3; Write-Output ('HTTP ' + $r.StatusCode) } catch { Write-Output ('HTTP_FAIL ' + $_.Exception.Message); exit 1 }"

echo.
echo [5] 日志文件路径
echo DEPLOY_LOG=%DEPLOY_LOG%
echo UI_LOG=%UI_LOG%
echo UI_DEBUG_LOG=%UI_DEBUG_LOG%
echo LIVE_LOG=%LIVE_LOG%

echo.
if exist %DEPLOY_LOG% (
  echo ===== one_click_deploy.log (tail 60) =====
  powershell -NoProfile -Command "Get-Content -Tail 60 '%DEPLOY_LOG%'"
) else (
  echo [WARN] 未找到 %DEPLOY_LOG%
)

echo.
if exist %UI_LOG% (
  echo ===== ui_server.log (tail 80) =====
  powershell -NoProfile -Command "Get-Content -Tail 80 '%UI_LOG%'"
) else (
  echo [WARN] 未找到 %UI_LOG%
)

echo.
if exist %UI_DEBUG_LOG% (
  echo ===== ui_app_debug.log (tail 80) =====
  powershell -NoProfile -Command "Get-Content -Tail 80 '%UI_DEBUG_LOG%'"
) else (
  echo [WARN] 未找到 %UI_DEBUG_LOG%
)

echo.
if exist %LIVE_LOG% (
  echo ===== ui_live.log (tail 80) =====
  powershell -NoProfile -Command "Get-Content -Tail 80 '%LIVE_LOG%'"
) else (
  echo [WARN] 未找到 %LIVE_LOG%
)

echo.
echo [DONE] 诊断完成，请将以上输出发给开发者。
pause
endlocal
