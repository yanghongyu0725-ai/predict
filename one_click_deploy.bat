@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set UI_HOST=127.0.0.1
set UI_PORT=8501
set UI_URL=http://%UI_HOST%:%UI_PORT%
set LOG_DIR=runtime
set DEPLOY_LOG=%LOG_DIR%\one_click_deploy.log
set UI_LOG=%LOG_DIR%\ui_server.log
set VENV_PYTHON=.venv\Scripts\python.exe

if not exist %LOG_DIR% mkdir %LOG_DIR%

echo ==================================================>>%DEPLOY_LOG%
echo [%date% %time%] one_click_deploy start >>%DEPLOY_LOG%
echo UI_URL=%UI_URL% >>%DEPLOY_LOG%

echo [INFO] 部署日志: %DEPLOY_LOG%
echo [INFO] UI日志: %UI_LOG%


echo [STEP] 1/5 Setup environment...
call setup_env.bat
if errorlevel 1 (
  echo [ERROR] 环境初始化失败，请检查网络/代理后重试。
  echo [%date% %time%] ERROR setup_env failed >>%DEPLOY_LOG%
  pause
  exit /b 1
)

echo [STEP] 2/5 Activate virtual env...
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ERROR] 虚拟环境激活失败。
  echo [%date% %time%] ERROR venv activate failed >>%DEPLOY_LOG%
  pause
  exit /b 1
)

if not exist %VENV_PYTHON% (
  echo [ERROR] 未找到虚拟环境解释器: %VENV_PYTHON%
  echo [%date% %time%] ERROR venv python missing: %VENV_PYTHON% >>%DEPLOY_LOG%
  pause
  exit /b 1
)

echo [INFO] 使用虚拟环境解释器: %VENV_PYTHON%
%VENV_PYTHON% -c "import sys;print(sys.executable)"
%VENV_PYTHON% -c "import sys;print(sys.executable)" >>%DEPLOY_LOG% 2>&1

echo [STEP] 3/5 Check dependencies...
%VENV_PYTHON% scripts/check_env.py --mode ui
%VENV_PYTHON% scripts/check_env.py --mode ui >>%DEPLOY_LOG% 2>&1
if errorlevel 1 (
  echo [WARN] UI依赖检查失败，尝试执行完整依赖安装...
  echo [%date% %time%] WARN check_env(ui) failed, trying full requirements install >>%DEPLOY_LOG%
  %VENV_PYTHON% -m pip install -r requirements.txt >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% -m pip uninstall -y python-dateutil >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% -m pip install --no-cache-dir --force-reinstall python-dateutil pandas >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% -m pip show python-dateutil >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% -c "import dateutil,sys;print('dateutil_ok',dateutil.__file__,sys.executable)" >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% scripts/check_env.py --mode ui >>%DEPLOY_LOG% 2>&1
  if errorlevel 1 (
    echo [ERROR] UI依赖检查仍未通过，请查看 %DEPLOY_LOG%
    echo [ERROR] 可手动执行: %VENV_PYTHON% -m pip install --no-cache-dir --force-reinstall python-dateutil pandas
    echo [%date% %time%] ERROR check_env(ui) failed after dateutil repair >>%DEPLOY_LOG%
    pause
    exit /b 1
  )
)

%VENV_PYTHON% scripts/check_env.py --mode full >>%DEPLOY_LOG% 2>&1
if errorlevel 1 (
  echo [WARN] 全量策略依赖未通过（不影响UI启动），后续运行策略时可能失败。
  echo [%date% %time%] WARN check_env(full) failed; continue for UI only >>%DEPLOY_LOG%
)

%VENV_PYTHON% -c "import sys;import flask,ccxt,pandas,plotly,dateutil;print('ui-import-check-ok')" >>%DEPLOY_LOG% 2>&1
if errorlevel 1 (
  echo [WARN] UI关键依赖导入失败，尝试自动修复 flask/ccxt/pandas/plotly/python-dateutil...
  echo [%date% %time%] WARN ui import check failed, installing ui deps >>%DEPLOY_LOG%
  %VENV_PYTHON% -m pip install --upgrade --force-reinstall python-dateutil pandas >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% -m pip install flask ccxt plotly >>%DEPLOY_LOG% 2>&1
  %VENV_PYTHON% -c "import sys;import flask,ccxt,pandas,plotly,dateutil;print('ui-import-check-ok')" >>%DEPLOY_LOG% 2>&1
  if errorlevel 1 (
    echo [ERROR] 关键依赖导入失败，请查看 %DEPLOY_LOG%
    pause
    exit /b 1
  )
)

echo [STEP] 4/5 Start UI process...
echo [%date% %time%] starting ui server >>%DEPLOY_LOG%
start "Strategy UI Server" cmd /k "call .venv\Scripts\activate.bat && %VENV_PYTHON% ui_app.py 1>>%UI_LOG% 2>&1"

echo [STEP] 5/5 Check UI health and open browser...
for /l %%i in (1,1,15) do (
  powershell -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-NetTCPConnection -LocalPort %UI_PORT% -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty State" >>%DEPLOY_LOG% 2>&1
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
echo [TIP] 可运行 diagnose_ui.bat 一键收集诊断信息
echo [%date% %time%] WARN ui not ready in timeout window >>%DEPLOY_LOG%
if exist %UI_LOG% (
  echo -------- ui_server.log 最后40行 --------
  powershell -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Content -Encoding UTF8 -Tail 40 '%UI_LOG%'"
)
start "Strategy UI" %UI_URL%

:done
endlocal
