@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"

echo ============================================================
echo   月球熔岩管多智能体网络沙盘 - 启动器
echo ============================================================
echo.

where py >nul 2>&1
if not errorlevel 1 goto use_py
where python >nul 2>&1
if errorlevel 1 goto no_python
set "PYCMD=python"
goto python_ok

:use_py
set "PYCMD=py -3"

:python_ok
%PYCMD% -m pip --version >nul 2>&1
if errorlevel 1 goto no_pip

where node >nul 2>&1
if errorlevel 1 goto no_node

REM ---------- 后端依赖检测 / 安装 ----------
%PYCMD% -c "import fastapi, uvicorn, websockets" >nul 2>&1
if not errorlevel 1 goto backend_ready
echo [依赖] 后端依赖未安装, 正在安装 (%PYCMD% -m pip install) ...
%PYCMD% -m pip install -r "%ROOT%backend\requirements.txt"
if errorlevel 1 goto backend_fail

:backend_ready
echo [依赖] 后端依赖已就绪。

REM ---------- 前端依赖检测 / 安装 ----------
if exist "%ROOT%frontend\node_modules" goto frontend_ready
echo [依赖] 前端依赖未安装, 正在安装 (npm install, 可能较慢) ...
pushd "%ROOT%frontend"
call npm install
set "NPM_ERR=%errorlevel%"
popd
if not "%NPM_ERR%"=="0" goto frontend_fail

:frontend_ready
echo [依赖] 前端依赖已就绪。

echo.
echo [1/2] 启动后端 FastAPI 服务 (端口 5000) ...
start "LavaTube-Backend-5000" /d "%ROOT%backend" cmd /k %PYCMD% main.py

echo [2/2] 启动前端 Vite 开发服务器 (端口 5173) ...
start "LavaTube-Frontend-5173" /d "%ROOT%frontend" cmd /k npm run dev

echo.
echo 启动完成!
echo   后端: http://localhost:5000
echo   前端: http://localhost:5173
echo.
echo 提示: 直接关闭本窗口不会影响已启动的服务。
echo       需要停止时请双击 stop.bat。
echo.
pause
exit /b 0

:no_python
echo [错误] 未检测到 Python (py/python), 请先安装并加入 PATH。
pause
exit /b 1

:no_pip
echo [错误] 当前 Python 解释器 (%PYCMD%) 缺少 pip。
pause
exit /b 1

:no_node
echo [错误] 未检测到 node, 请先安装并加入 PATH。
pause
exit /b 1

:backend_fail
echo [错误] 后端依赖安装失败, 请手动执行:
echo        cd backend ^&^& pip install -r requirements.txt
pause
exit /b 1

:frontend_fail
echo [错误] 前端依赖安装失败, 请手动执行:
echo        cd frontend ^&^& npm install
pause
exit /b 1
