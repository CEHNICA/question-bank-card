@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "QB_INSTALLED_DIR=%LOCALAPPDATA%\Programs\QuestionBankCard"
set "QB_INSTALLED_APP=%QB_INSTALLED_DIR%\QuestionBankCard.exe"
set "QB_INSTALLED_CLI=%QB_INSTALLED_DIR%\tiyouju.exe"
if exist "%QB_INSTALLED_APP%" goto use_installed
if exist "%QB_INSTALLED_CLI%" goto use_installed
set "QB_VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%QB_VENV_PY%" (
  echo 未找到安装版，源码运行环境也未准备好。
  echo 请先安装题有据，或双击“启动题有据.cmd”准备源码环境，再运行本工具。
  set "QB_SHORTCUT_RESULT=1"
  goto finished
)
"%QB_VENV_PY%" "%~dp0create_shortcut.py"
set "QB_SHORTCUT_RESULT=%ERRORLEVEL%"
goto finished
:use_installed
if not exist "%QB_INSTALLED_APP%" goto incomplete_install
if not exist "%QB_INSTALLED_CLI%" goto incomplete_install
"%QB_INSTALLED_CLI%" assistant-setup --help >nul 2>nul
if errorlevel 1 (
  echo 已安装的题有据不支持新版图标命令。请升级后再创建桌面图标。
  set "QB_SHORTCUT_RESULT=1"
  goto finished
)
set "TIYOUJU_APP=%QB_INSTALLED_APP%"
"%QB_INSTALLED_CLI%" assistant-setup --desktop show
set "QB_SHORTCUT_RESULT=%ERRORLEVEL%"
goto finished
:incomplete_install
echo 已找到安装目录，但程序或配套命令不完整。请重新安装或升级题有据。
set "QB_SHORTCUT_RESULT=1"
:finished
if not "%QB_SHORTCUT_RESULT%"=="0" echo 创建没有完成，请查看上方原因；已保留不相关的图标。
echo.
echo 按任意键关闭。
pause >nul
exit /b %QB_SHORTCUT_RESULT%
