@echo off
setlocal

set UI_HOST=127.0.0.1
set UI_PORT=8501
set UI_URL=http://%UI_HOST%:%UI_PORT%

echo [STEP] 1/5 初始化运行环境...
call setup_env.bat
if errorlevel 1 (
  echo [ERROR] 环境初始化失败，请检查网络/代理后重试。
  pause
  exit /b 1
)

echo [STEP] 2/5 激活虚拟环境...
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ERROR] 虚拟环境激活失败。
  pause
  exit /b 1
)

echo [STEP] 3/5 运行依赖检查...
python scripts/check_env.py
if errorlevel 1 (
  echo [ERROR] 依赖仍缺失，尝试自动补装 Flask 后重试检查...
  python -m pip install flask
  python scripts/check_env.py
  if errorlevel 1 (
    echo [ERROR] 依赖检查仍未通过，请先排查 pip 网络连接问题。
    pause
    exit /b 1
  )
)

echo [STEP] 4/5 启动本地UI进程...
if not exist runtime mkdir runtime
start "Strategy UI Server" cmd /k "call .venv\Scripts\activate.bat && python ui_app.py 1>>runtime\ui_server.log 2>&1"

echo [STEP] 5/5 检查服务状态并打开浏览器...
for /l %%i in (1,1,15) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri '%UI_URL%' -TimeoutSec 2; if($r.StatusCode -ge 200){ exit 0 } else { exit 1 } } catch { exit 1 }"
  if not errorlevel 1 (
    start "Strategy UI" %UI_URL%
    echo [OK] UI已启动: %UI_URL%
    goto :done
  )
  timeout /t 1 >nul
)

echo [WARN] 未检测到UI已就绪，请查看日志: runtime\ui_server.log
start "Strategy UI" %UI_URL%

:done
endlocal
