@echo off
setlocal

set PYTHON_BIN=python
set VENV_DIR=.venv

%PYTHON_BIN% -m venv %VENV_DIR%
call %VENV_DIR%\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [OK] 环境安装完成 (CMD)
echo 激活方式: call %VENV_DIR%\Scripts\activate.bat
echo 环境检查: python scripts/check_env.py
endlocal
