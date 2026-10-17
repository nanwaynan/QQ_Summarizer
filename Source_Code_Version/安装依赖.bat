@echo off
chcp 936 >nul 2>nul
cd /d "%~dp0"
title QQ 群聊总结器 - 安装依赖
setlocal EnableDelayedExpansion

echo ============================================
echo   QQ 群聊总结器 - 依赖安装
echo ============================================
echo.
echo 本脚本会：
echo   1. 检查你的 Python 版本
echo   2. 在本目录创建虚拟环境 .venv
echo   3. 安装 requirements.txt 里的依赖
echo.
echo 注意：安装过程需要联网，约需 2-5 分钟。
echo.
pause

REM ============ 1. 查找 Python ============
echo.
echo [1/3] 正在查找 Python 3.12 ...
set "SYS_PY="

REM 优先 py 启动器（可指定版本）
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "SYS_PY=py -3.12"
    echo       找到 py -3.12
    goto :found
)

py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "SYS_PY=py -3"
    echo       找到 py -3
    goto :found
)

python -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "SYS_PY=python"
    echo       找到 python
    goto :found
)

REM 常见安装路径兜底
for %%d in (
    "D:\Python 3.12\python.exe"
    "C:\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) do (
    if exist %%d (
        set "SYS_PY=%%~d"
        echo       在 %%d 找到 Python
        goto :found
    )
)

echo.
echo [错误] 未找到 Python
echo.
echo 请先安装 Python 3.12（安装时勾选 "Add Python to PATH"）：
echo   https://www.python.org/downloads/
echo.
echo 安装完成后重新运行本脚本。
echo.
pause
exit /b 1

:found
REM ============ 2. 创建虚拟环境 ============
echo.
echo [2/3] 正在创建虚拟环境 .venv ...
if exist ".venv\Scripts\python.exe" (
    echo       已存在，跳过
) else (
    %SYS_PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo [错误] 创建虚拟环境失败
        echo        请确认 Python 安装完整（含 venv 模块）
        echo.
        pause
        exit /b 1
    )
    echo       创建完成
)

set "PY=%~dp0.venv\Scripts\python.exe"

REM ============ 3. 安装依赖 ============
echo.
echo [3/3] 正在安装依赖（使用清华镜像加速）...
echo.

"%PY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo       [警告] pip 升级失败，继续尝试安装依赖
)

"%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败
    echo.
    echo 可能原因：
    echo   1. 网络问题 —— 检查网络后重试
    echo   2. 镜像源不可用 —— 可手动执行：
    echo      .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！
echo ============================================
echo.
echo 下一步：
echo   1. 双击「启动.bat」启动程序
echo   2. 首次启动会弹出引导，按提示填写 AI 接口配置
echo      （API Key 必须填自己的，模板里的 YOUR_API_KEY_HERE 不可用）
echo   3. 还需要 NapCat 才能拉取 QQ 消息
echo      如果本目录没有 napcat_green 文件夹，
echo      请使用「Bat_Startup_Version」或「EXE_Single_File_Version」版本，
echo      它们已内置 NapCat。
echo.
pause
