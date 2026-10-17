@echo off
chcp 936 >nul 2>nul
cd /d "%~dp0"
title QQ 群聊总结器（源码版）

echo ============================================
echo   QQ 群聊总结器 - 源码版
echo ============================================
echo.

REM ---- 检查虚拟环境 ----
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [错误] 未找到 Python 虚拟环境
    echo.
    echo 请先运行本目录下的「安装依赖.bat」完成环境准备。
    echo 该脚本会自动创建 .venv 并安装所需依赖（需要联网）。
    echo.
    pause
    exit /b 1
)

REM ---- 优先用 pythonw（无控制台窗口）----
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0main.py" %*
) else (
    start "" "%PY%" "%~dp0main.py" %*
)

exit /b 0
