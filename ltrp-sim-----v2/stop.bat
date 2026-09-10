@echo off
chcp 65001 >nul
title LTRP 一键停止

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5000,5174,5175 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
taskkill /F /IM cloudflared.exe >nul 2>&1

echo 已停止 LTRP 后端 / 前端 / 内网穿透隧道(端口 5000/5174/5175)。
echo.
pause
