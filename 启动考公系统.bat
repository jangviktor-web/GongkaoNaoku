@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUNBUFFERED=1"

set "PY="
if exist "D:\Python310\python.exe" set "PY=D:\Python310\python.exe"
if defined PY goto :havepy
where python >nul 2>nul && set "PY=python"
if defined PY goto :havepy
where py >nul 2>nul && set "PY=py"
if defined PY goto :havepy
echo [ERROR] Python not found. Install Python 3, or edit this .bat and set PY to your python.exe path.
pause
exit /b 1

:havepy
echo [INFO] Using Python: %PY%

if exist kaogong.db goto :run
echo [INFO] First run: importing 11000+ questions into local database, about 30s, please wait...
"%PY%" parse.py
if errorlevel 1 goto :impfail

:run
echo [INFO] Starting local server at  http://127.0.0.1:8300
echo [INFO] Browser will open automatically. Keep this window OPEN; closing it stops the server.
start "" "http://127.0.0.1:8300"
"%PY%" server.py
echo.
echo [INFO] Server has stopped.
pause
exit /b 0

:impfail
echo [ERROR] Import failed. Make sure the folder "kaogongzhentizhengliu-main" is right beside this .bat
pause
exit /b 1
