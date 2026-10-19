@echo off
chcp 65001 >nul
title Package DeepSeek Harness
echo ==============================================
echo   DeepSeek Harness migration packager
echo ==============================================
echo.
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0package-dsh.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package-dsh.ps1" %*
)
echo.
echo Options you can pass through:
echo   -IncludeApp          also pack the ~1 GB program files
echo   -IncludeWorkspace    also pack %CD%\question_card_bank sources
echo   -ExcludeCredentials  leave out the login/API-key file
echo   -OutRoot D:\path     write the zip somewhere else
echo.
pause
