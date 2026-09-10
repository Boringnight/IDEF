@echo off
chcp 65001 >nul
title LTRP 一键启动(含内网穿透,启动 ltrp-sim)

echo [1/4] 清理可能残留的旧进程(后端/前端/穿透隧道)...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5000,5174,5175 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
taskkill /F /IM cloudflared.exe >nul 2>&1

echo [2/4] 启动 ltrp-sim 后端(:5000)与前端(:5174)...
start "LTRP Backend :5000" /d "%~dp0backend" cmd /k "py -3.13 main.py"
start "LTRP Frontend" /d "%~dp0frontend" cmd /k "npm run dev"

echo [3/4] 等待前端就绪(约5秒)...
timeout /t 5 /nobreak >nul

echo [4/4] 启动内网穿透隧道并自动获取网址...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tunnel.ps1"

echo.
echo 启动完成！(当前运行:ltrp-sim 演示版)
echo   后端 : http://localhost:5000
echo   前端 : http://localhost:5174
echo   穿透 : 上方绿色高亮即网址,已复制到剪贴板(分享时直接 Ctrl+V)。
echo.
echo 停止服务请双击 stop.bat
echo.
pause
