@echo off
setlocal

if exist scripts\setup_env.bat (
  call scripts\setup_env.bat
) else (
  echo [ERROR] 未找到 scripts\setup_env.bat
  echo 你当前目录可能不是项目根目录，或者本地代码还没更新。
  echo 请先执行: dir
  echo 再执行: git pull
  exit /b 1
)

endlocal
