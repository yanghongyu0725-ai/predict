@echo off
setlocal
call setup_env.bat
call .venv\Scripts\activate.bat
python -m pip install flask
start "Strategy UI" http://127.0.0.1:8501
python ui_app.py
endlocal
