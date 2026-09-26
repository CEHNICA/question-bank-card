@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "QB_VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%QB_VENV_PY%" (
  echo 还没有建立运行环境。请先双击“启动题库题卡版.cmd”完成第一次安装，再运行本工具。
  echo.
  echo 按任意键关闭。
  pause >nul
  exit /b 1
)
"%QB_VENV_PY%" "%~dp0create_shortcut.py"
echo.
echo 按任意键关闭。
pause >nul
