@echo off
chcp 936 >nul 2>nul
cd /d "%~dp0"
title QQ 群聊总结器（单文件版）

echo ============================================
echo   QQ 群聊总结器 - 单文件版
echo ============================================
echo.

if not exist "%~dp0QQ总结器.exe" (
    echo [错误] 找不到 QQ总结器.exe
    echo.
    echo 请确认本目录下有以下结构：
    echo   EXE_Single_File_Version\
    echo   ├── 启动.bat          ^<-- 正在运行
    echo   ├── QQ总结器.exe       ^<-- 需要这个
    echo   └── napcat_green\
    echo.
    pause
    exit /b 1
)

echo 正在启动...
echo 提示：单文件版首次启动需要自解压，约 5-15 秒，请耐心等待。
echo.
start "" "%~dp0QQ总结器.exe"
exit /b 0
