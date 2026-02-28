param([string]$Branch = "main")
if (Test-Path "scripts/force_sync.sh") {
    bash scripts/force_sync.sh $Branch
} elseif (Test-Path "scripts/force_sync.bat") {
    & "scripts/force_sync.bat" $Branch
} else {
    Write-Host "[ERROR] 未找到 force_sync 脚本"
    exit 1
}
