@echo off
chcp 936 >nul 2>nul
cd /d "%~dp0"
title QQ 群聊总结器（文件夹版）

echo ============================================
echo   QQ 群聊总结器 - 文件夹版
echo ============================================
echo.

if not exist "%~dp0文件夹版\QQ总结器.exe" (
    echo [错误] 找不到 文件夹版\QQ总结器.exe
    echo.
    echo 请确认本目录下有以下结构：
    echo   Bat_Startup_Version\
    echo   ├── 启动.bat          ^<-- 正在运行
    echo   ├── 文件夹版\
    echo   │   ├── QQ总结器.exe   ^<-- 需要这个
    echo   │   └── _internal\
    echo   └── napcat_green\
    echo.
    pause
    exit /b 1
)

echo 正在启动...
start "" "%~dp0文件夹版\QQ总结器.exe"
exit /b 0
