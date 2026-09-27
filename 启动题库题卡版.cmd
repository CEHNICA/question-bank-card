@echo off
rem 兼容旧书签和旧快捷方式；新入口是“启动题有据.cmd”。
call "%~dp0启动题有据.cmd"
exit /b %errorlevel%
