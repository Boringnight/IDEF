@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo   月球熔岩管多智能体网络沙盘 - 停止器
echo ============================================================
echo.

echo [1/2] 停止后端 (端口 5000) ...
set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do (
    set "FOUND=1"
    echo    停止进程 PID=%%a
    taskkill /F /PID %%a >nul 2>&1
)
if not defined FOUND echo    端口 5000 当前无监听进程。

echo [2/2] 停止前端 (端口 5173) ...
set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    set "FOUND=1"
    echo    停止进程 PID=%%a
    taskkill /F /PID %%a >nul 2>&1
)
if not defined FOUND echo    端口 5173 当前无监听进程。

echo.
echo 停止操作完成。
echo.
pause
