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
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if not errorlevel 1 goto use_python
echo 未找到可用的 Python 3.12。请先安装 Python 3.12。
pause >nul
exit /b 1
:use_venv
"%QB_VENV_PY%" "%~dp0start_question_bank.py"
goto finished
:use_py
py -3.12 "%~dp0start_question_bank.py"
goto finished
:use_python
python "%~dp0start_question_bank.py"
:finished
if errorlevel 1 (
  echo.
  echo 启动没有完成。请查看上方提示，然后按任意键关闭。
  pause >nul
  exit /b 1
)
