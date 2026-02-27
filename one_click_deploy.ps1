$ErrorActionPreference = "Stop"

Write-Host "[STEP] 1/4 初始化运行环境..."
& ".\setup_env.ps1"

Write-Host "[STEP] 2/4 激活虚拟环境..."
& ".\.venv\Scripts\Activate.ps1"

Write-Host "[STEP] 3/4 运行依赖检查..."
python scripts/check_env.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] 依赖缺失，尝试自动补装 Flask 后重试..."
    python -m pip install flask
    python scripts/check_env.py
    if ($LASTEXITCODE -ne 0) {
        throw "依赖检查失败，请检查网络或代理设置。"
    }
}

Write-Host "[STEP] 4/4 启动本地UI..."
Start-Process "http://127.0.0.1:8501"
python ui_app.py
