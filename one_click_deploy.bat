@echo off
setlocal

set UI_HOST=127.0.0.1
set UI_PORT=8501
set UI_URL=http://%UI_HOST%:%UI_PORT%
set LOG_DIR=runtime
set DEPLOY_LOG=%LOG_DIR%\one_click_deploy.log
set UI_LOG=%LOG_DIR%\ui_server.log

if not exist %LOG_DIR% mkdir %LOG_DIR%

echo ==================================================>>%DEPLOY_LOG%
echo [%date% %time%] one_click_deploy start >>%DEPLOY_LOG%
echo UI_URL=%UI_URL% >>%DEPLOY_LOG%

echo [STEP] 1/5 初始化运行环境...
call setup_env.bat
if errorlevel 1 (
  echo [ERROR] 环境初始化失败，请检查网络/代理后重试。
  echo [%date% %time%] ERROR setup_env failed >>%DEPLOY_LOG%
  pause
  exit /b 1
)

echo [STEP] 2/5 激活虚拟环境...
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ERROR] 虚拟环境激活失败。
  echo [%date% %time%] ERROR venv activate failed >>%DEPLOY_LOG%
  pause
  exit /b 1
)

echo [STEP] 3/5 运行依赖检查...
python scripts/check_env.py >>%DEPLOY_LOG% 2>&1
if errorlevel 1 (
  echo [WARN] 依赖检查失败，尝试执行完整依赖安装...
  echo [%date% %time%] WARN check_env failed, trying full requirements install >>%DEPLOY_LOG%
  python -m pip install -r requirements.txt >>%DEPLOY_LOG% 2>&1
  python scripts/check_env.py >>%DEPLOY_LOG% 2>&1
  if errorlevel 1 (
    echo [ERROR] 依赖检查仍未通过，请查看 %DEPLOY_LOG%
    echo [%date% %time%] ERROR check_env failed after requirements reinstall >>%DEPLOY_LOG%
    pause
    exit /b 1
  )
)

python -c "import importlib.util,sys;mods=['flask','ccxt','pandas','plotly'];miss=[m for m in mods if importlib.util.find_spec(m) is None];print('missing=' + ','.join(miss));sys.exit(1 if miss else 0)" >>%DEPLOY_LOG% 2>&1
if errorlevel 1 (
  echo [WARN] UI关键依赖缺失，尝试自动安装 flask/ccxt/pandas/plotly...
  echo [%date% %time%] WARN ui import check failed, installing ui deps >>%DEPLOY_LOG%
  python -m pip install flask ccxt pandas plotly >>%DEPLOY_LOG% 2>&1
  python -c "import importlib.util,sys;mods=['flask','ccxt','pandas','plotly'];miss=[m for m in mods if importlib.util.find_spec(m) is None];print('missing=' + ','.join(miss));sys.exit(1 if miss else 0)" >>%DEPLOY_LOG% 2>&1
  if errorlevel 1 (
    echo [ERROR] 关键依赖导入失败，请查看 %DEPLOY_LOG%
    pause
    exit /b 1
  )
)

echo [STEP] 4/5 启动本地UI进程...
echo [%date% %time%] starting ui server >>%DEPLOY_LOG%
start "Strategy UI Server" cmd /k "call .venv\Scripts\activate.bat && python ui_app.py 1>>%UI_LOG% 2>&1"

echo [STEP] 5/5 检查服务状态并打开浏览器...
for /l %%i in (1,1,15) do (
  powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %UI_PORT% -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty State" >>%DEPLOY_LOG% 2>&1
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri '%UI_URL%' -TimeoutSec 2; if($r.StatusCode -ge 200){ exit 0 } else { exit 1 } } catch { exit 1 }"
  if not errorlevel 1 (
    start "Strategy UI" %UI_URL%
    echo [OK] UI已启动: %UI_URL%
    echo [%date% %time%] ui ready and browser opened >>%DEPLOY_LOG%
    goto :done
  )
  timeout /t 1 >nul
)

echo [WARN] 未检测到UI已就绪，请查看日志: %UI_LOG%
echo [WARN] 部署日志: %DEPLOY_LOG%
echo [%date% %time%] WARN ui not ready in timeout window >>%DEPLOY_LOG%
if exist %UI_LOG% (
  echo -------- ui_server.log 最后40行 --------
  powershell -NoProfile -Command "Get-Content -Tail 40 '%UI_LOG%'"
)
start "Strategy UI" %UI_URL%

:done
endlocal
