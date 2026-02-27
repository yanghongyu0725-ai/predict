@echo off
setlocal

echo [STEP] 1/4 初始化运行环境...
call setup_env.bat
if errorlevel 1 (
  echo [ERROR] 环境初始化失败，请检查网络/代理后重试。
  exit /b 1
)

echo [STEP] 2/4 激活虚拟环境...
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ERROR] 虚拟环境激活失败。
  exit /b 1
)

echo [STEP] 3/4 运行依赖检查...
python scripts/check_env.py
if errorlevel 1 (
  echo [ERROR] 依赖仍缺失，尝试自动补装 Flask 后重试检查...
  python -m pip install flask
  python scripts/check_env.py
  if errorlevel 1 (
    echo [ERROR] 依赖检查仍未通过，请先排查 pip 网络连接问题。
    exit /b 1
  )
)

echo [STEP] 4/4 启动本地UI...
start "Strategy UI" http://127.0.0.1:8501
python ui_app.py
endlocal
