@echo off
setlocal
if exist scripts\force_sync.bat (
  call scripts\force_sync.bat %1
) else (
  echo [ERROR] 未找到 scripts\force_sync.bat
  exit /b 1
)
endlocal
