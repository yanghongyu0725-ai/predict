param(
    [string]$PythonBin = "python",
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"

& $PythonBin -m venv $VenvDir
& "$VenvDir\Scripts\Activate.ps1"
python -m pip install --upgrade pip
pip install -r requirements.txt

Write-Host "[OK] 环境安装完成 (PowerShell)"
Write-Host "激活方式: .\$VenvDir\Scripts\Activate.ps1"
Write-Host "环境检查: python scripts/check_env.py"
