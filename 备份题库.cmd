@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "QB_VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
if exist "%QB_VENV_PY%" (
  "%QB_VENV_PY%" "%~dp0backup_question_bank.py"
) else (
  py -3.12 "%~dp0backup_question_bank.py"
)
echo.
pause
