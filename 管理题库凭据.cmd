@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "QB_VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
if exist "%QB_VENV_PY%" (
  "%QB_VENV_PY%" --version >nul 2>nul
  if not errorlevel 1 goto use_venv
)
py -3.12 --version >nul 2>nul
if not errorlevel 1 goto use_py
python --version >nul 2>nul
if not errorlevel 1 goto use_python
echo 未找到可用的 Python。请先安装 Python 3.12 或更新版本。
set "QB_RESULT=1"
goto pause_end
:use_venv
"%QB_VENV_PY%" "%~dp0credential_store.py"
goto finished
:use_py
py -3.12 "%~dp0credential_store.py"
goto finished
:use_python
python "%~dp0credential_store.py"
:finished
set "QB_RESULT=%ERRORLEVEL%"
:pause_end
echo.
echo 按任意键关闭。
pause >nul
exit /b %QB_RESULT%
