$ErrorActionPreference = "Stop"
if (Test-Path "scripts/setup_env.ps1") {
    & "scripts/setup_env.ps1"
} else {
    Write-Host "[ERROR] 未找到 scripts/setup_env.ps1"
    Write-Host "你当前目录可能不是项目根目录，或者本地代码还没更新。"
    Write-Host "请先执行: dir"
    Write-Host "再执行: git pull"
    exit 1
}
