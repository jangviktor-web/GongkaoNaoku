@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUNBUFFERED=1"

set "PY="
if exist "D:\Python310\python.exe" set "PY=D:\Python310\python.exe"
if defined PY goto :havepy
where python >nul 2>nul && set "PY=python"
if defined PY goto :havepy
echo [ERROR] Python not found.
pause
exit /b 1

:havepy
echo [INFO] Re-importing question bank. Your study progress (by question id) is KEPT.
echo [INFO] Only brand-new questions are added as "new". This does NOT reset progress.
echo.
"%PY%" parse.py
if errorlevel 1 (echo [ERROR] Re-import failed. Check the kaogongzhentizhengliu-main folder is beside this .bat & pause & exit /b 1)
echo [OK] Re-import done.
pause
