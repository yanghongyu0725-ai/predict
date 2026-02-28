@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "UI_HOST=127.0.0.1"
set "UI_PORT=8501"
set "UI_URL=http://%UI_HOST%:%UI_PORT%"
set "LOG_DIR=runtime"
set "DEPLOY_LOG=%LOG_DIR%\one_click_deploy.log"
set "UI_LOG=%LOG_DIR%\ui_server.log"
set "VENV_PYTHON=.venv\Scripts\python.exe"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ==================================================>"%DEPLOY_LOG%"
echo [%date% %time%] one_click_deploy start >>"%DEPLOY_LOG%"
echo UI_URL=%UI_URL% >>"%DEPLOY_LOG%"
if exist "%UI_LOG%" del /q "%UI_LOG%"

echo [INFO] Deploy log: %DEPLOY_LOG%
echo [INFO] UI log: %UI_LOG%

echo [STEP] 1/5 Setup environment...
echo [%date% %time%] STEP1 setup_env begin >>"%DEPLOY_LOG%"
call setup_env.bat
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP1 setup_env rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_setup

echo [STEP] 2/5 Activate virtual env...
echo [%date% %time%] STEP2 activate begin >>"%DEPLOY_LOG%"
call .venv\Scripts\activate.bat
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP2 activate rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_activate

if not exist "%VENV_PYTHON%" goto :fail_missing_venv_python

echo [INFO] Python in use: %VENV_PYTHON%
"%VENV_PYTHON%" -c "import sys;print(sys.executable)"
"%VENV_PYTHON%" -c "import sys;print(sys.executable)" >>"%DEPLOY_LOG%" 2>&1

echo [STEP] 3/5 Check dependencies...
echo [%date% %time%] STEP3 check_env(ui) begin >>"%DEPLOY_LOG%"
"%VENV_PYTHON%" scripts/check_env.py --mode ui
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 check_env(ui) rc=%RC% >>"%DEPLOY_LOG%"
if "%RC%"=="0" goto :check_full

echo [WARN] UI dependency check failed, start repair...
echo [%date% %time%] WARN check_env(ui) failed, start repair >>"%DEPLOY_LOG%"
"%VENV_PYTHON%" -m pip install -r requirements.txt >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" -m pip uninstall -y python-dateutil >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" -m pip install --no-cache-dir --force-reinstall python-dateutil pandas plotly flask ccxt >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" -m pip show python-dateutil >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" -c "import dateutil,sys;print('dateutil_ok',dateutil.__file__,sys.executable)" >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" scripts/check_env.py --mode ui >>"%DEPLOY_LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 repair ui-check rc=%RC% >>"%DEPLOY_LOG%"
if "%RC%"=="0" goto :check_full

echo [WARN] UI deps still broken, recreating virtual env...
echo [%date% %time%] WARN recreate venv because dateutil still broken >>"%DEPLOY_LOG%"
if exist ".venv" rmdir /s /q ".venv"
python -m venv .venv >>"%DEPLOY_LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 recreate venv rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_recreate_venv

call .venv\Scripts\activate.bat
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 re-activate rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_activate

set "VENV_PYTHON=.venv\Scripts\python.exe"
"%VENV_PYTHON%" -m pip install --upgrade pip >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" -m pip install -r requirements.txt >>"%DEPLOY_LOG%" 2>&1
"%VENV_PYTHON%" scripts/check_env.py --mode ui >>"%DEPLOY_LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 recreate ui-check rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_ui_check

:check_full
echo [%date% %time%] STEP3 check_env(full) begin >>"%DEPLOY_LOG%"
"%VENV_PYTHON%" scripts/check_env.py --mode full >>"%DEPLOY_LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 check_env(full) rc=%RC% >>"%DEPLOY_LOG%"
if "%RC%"=="0" goto :ui_import_check

echo [WARN] Full strategy dependency check failed (UI can still start; strategy run may fail).
echo [%date% %time%] WARN check_env(full) failed; continue for UI only >>"%DEPLOY_LOG%"

:ui_import_check
echo [%date% %time%] STEP3 ui-import-check begin >>"%DEPLOY_LOG%"
"%VENV_PYTHON%" -c "import flask,ccxt,pandas,dateutil;import plotly.graph_objects as go;print('ui-import-check-ok')" >>"%DEPLOY_LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP3 ui-import-check rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_ui_check

echo [STEP] 4/5 Start UI process...
echo [%date% %time%] STEP4 start ui process begin >>"%DEPLOY_LOG%"
start "Strategy UI Server" /b "%VENV_PYTHON%" -u ui_app.py 1>>"%UI_LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] STEP4 start rc=%RC% >>"%DEPLOY_LOG%"
if not "%RC%"=="0" goto :fail_ui_check
timeout /t 2 >nul

echo [STEP] 5/5 Check UI health and open browser...
echo [%date% %time%] STEP5 healthcheck begin >>"%DEPLOY_LOG%"
for /l %%i in (1,1,20) do (
  echo [!date! !time!] healthcheck try %%i >>"%DEPLOY_LOG%"
  netstat -ano | findstr :%UI_PORT% >>"%DEPLOY_LOG%" 2>&1
  powershell -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; try { $r=Invoke-WebRequest -UseBasicParsing -Uri '%UI_URL%' -TimeoutSec 2; if($r.StatusCode -ge 200){ exit 0 } else { exit 1 } } catch { exit 1 }"
  if not errorlevel 1 (
    start "Strategy UI" %UI_URL%
    echo [OK] UI is ready: %UI_URL%
    echo [%date% %time%] ui ready and browser opened >>"%DEPLOY_LOG%"
    goto :done
  )
  timeout /t 1 >nul
)

echo [WARN] UI not ready, check logs:
echo [WARN] %UI_LOG%
echo [WARN] %DEPLOY_LOG%
echo [TIP] Run diagnose_ui.bat for one-click diagnostics.
echo [%date% %time%] WARN ui not ready in timeout window >>"%DEPLOY_LOG%"
if exist "%UI_LOG%" powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 120 '%UI_LOG%'" >>"%DEPLOY_LOG%" 2>&1
tasklist | findstr /I "python.exe" >>"%DEPLOY_LOG%" 2>&1
if exist "%UI_LOG%" (
  echo -------- ui_server.log tail 60 --------
  powershell -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Content -Encoding UTF8 -Tail 60 '%UI_LOG%'"
)
start "Strategy UI" %UI_URL%
goto :done

:fail_setup
echo [ERROR] setup_env failed.
echo [%date% %time%] ERROR setup_env failed >>"%DEPLOY_LOG%"
goto :exit_fail

:fail_activate
echo [ERROR] activate venv failed.
echo [%date% %time%] ERROR venv activate failed >>"%DEPLOY_LOG%"
goto :exit_fail

:fail_missing_venv_python
echo [ERROR] missing venv python: %VENV_PYTHON%
echo [%date% %time%] ERROR venv python missing >>"%DEPLOY_LOG%"
goto :exit_fail

:fail_recreate_venv
echo [ERROR] recreate venv failed. See %DEPLOY_LOG%
echo [%date% %time%] ERROR recreate venv failed >>"%DEPLOY_LOG%"
goto :exit_fail

:fail_ui_check
echo [ERROR] UI dependency/process check failed.
echo [ERROR] Try: .venv\Scripts\python.exe -m pip install --no-cache-dir --force-reinstall python-dateutil pandas plotly flask ccxt
echo [%date% %time%] ERROR ui dependency/process check failed >>"%DEPLOY_LOG%"
goto :exit_fail

:exit_fail
pause
exit /b 1

:done
endlocal
