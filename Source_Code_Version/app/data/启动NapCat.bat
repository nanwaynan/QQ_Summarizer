@echo off
chcp 936 >nul 2>nul
setlocal EnableDelayedExpansion
title NapCat 启动器

cd /d "%~dp0" || exit /b 1
set "LOGFILE=%~dp0napcat_launch.log"

REM ============ 0. 模式判定 ============
REM 自动模式（GUI 拉起）来源有两种，命中任一即启用：
REM   a) 命令行 --auto
REM   b) 环境变量 NAPCAT_AUTO=1（提权后仍能继承）
REM 自动模式下全程不提问、不 pause —— 因为窗口被最小化/隐藏时，
REM set /p 会永久阻塞，自动启动会静默卡死。
set "AUTO_MODE="
if /i "%~1"=="--auto" set "AUTO_MODE=1"
if /i "%NAPCAT_AUTO%"=="1" set "AUTO_MODE=1"
if defined AUTO_MODE (
    set "NAPCAT_AUTO=1"
    echo. >>"%LOGFILE%"
    echo [%DATE% %TIME%] ===== 自动模式启动 ===== >>"%LOGFILE%"
)

REM 透传给 NapCat 内核的参数：只在首个参数确为 --auto 时才剔除它。
REM 不能无条件做字符串替换 —— 变量为空时 cmd 的替换会产出 "--auto=" 这种
REM 垃圾并透传给内核，所以这里必须先判定 %~1。
set "PASS_ARGS=%*"
if /i "%~1"=="--auto" set "PASS_ARGS=!PASS_ARGS:--auto=!"

REM ============ 1. 拖拽支持：把 QQ.exe 拖到本脚本上 ============
if not "%~1"=="" (
    if /i "%~nx1"=="QQ.exe" (
        echo [记录] 收到 QQ 路径: %~1
        echo %~1 > "%~dp0qq_path.txt"
        echo        已保存到 qq_path.txt，后续会自动使用
        echo.
        if not defined AUTO_MODE timeout /t 2 >nul 2>&1
    )
)

echo ============================================
echo   NapCat 启动器（QQ 群聊总结器配套）
echo ============================================
echo.

REM ============ 2. 检查管理员权限 ============
net session >nul 2>&1
if errorlevel 1 (
    echo [提示] 需要管理员权限才能注入 QQ 客户端
    echo        正在请求提权，请在弹出的 UAC 窗口点「是」...
    if defined AUTO_MODE echo [%DATE% %TIME%] 请求提权（等待 UAC 确认） >>"%LOGFILE%"
    if defined AUTO_MODE (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--auto' -Verb RunAs" 2>>"%LOGFILE%"
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs" 2>nul
    )
    exit /b 0
)
echo [OK] 已获得管理员权限
echo.
if defined AUTO_MODE echo [%DATE% %TIME%] 已获得管理员权限 >>"%LOGFILE%"

REM ============ 3. 检查 QQ 是否在运行 ============
tasklist /FI "IMAGENAME eq QQ.exe" 2>nul | findstr /I "QQ.exe" >nul
if not errorlevel 1 (
    if defined AUTO_MODE (
        REM NapCat 必须在 QQ 启动时注入，所以这里必须关掉已运行的 QQ
        echo [提示] 检测到 QQ 正在运行，自动关闭以便注入 NapCat
        echo [%DATE% %TIME%] 检测到 QQ 在运行，自动关闭后重新注入 >>"%LOGFILE%"
        taskkill /F /IM QQ.exe >nul 2>&1
        taskkill /F /IM QQEX.exe >nul 2>&1
        ping -n 4 127.0.0.1 >nul
        echo        已关闭
    ) else (
        echo [提示] 检测到 QQ 正在运行
        echo        NapCat 需要以注入方式启动 QQ，请先关闭现有 QQ
        echo.
        set /p "ANSWER=是否自动关闭 QQ 并继续？(Y/N): "
        if /i "!ANSWER!"=="Y" (
            echo        正在关闭 QQ...
            taskkill /F /IM QQ.exe >nul 2>&1
            taskkill /F /IM QQEX.exe >nul 2>&1
            ping -n 4 127.0.0.1 >nul
            echo        已关闭
        ) else (
            echo.
            echo 已取消。请手动关闭 QQ 后重新运行本脚本。
            echo.
            pause
            exit /b 1
        )
    )
)
echo.

REM ============ 4. 探测 QQ 安装路径 ============
set "QQPath="

REM --- 4.1 用户手动指定（最高优先级）---
if exist "%~dp0qq_path.txt" (
    for /f "usebackq tokens=* delims=" %%i in ("%~dp0qq_path.txt") do (
        if exist "%%i" (
            set "QQPath=%%i"
            echo [OK] 使用 qq_path.txt 指定的路径
        )
    )
)

REM --- 4.2 从注册表读取（官方方式）---
REM 64 位系统上 QQ 可能注册在 64 位项，也可能在 WOW6432Node，两个都查。
if not defined QQPath call :QRY_REG "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ"
if not defined QQPath call :QRY_REG "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"

REM --- 4.2b 用户级安装 ---
if not defined QQPath call :QRY_REG "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ"

REM --- 4.3 常见安装路径 ---
if not defined QQPath (
    for %%p in (
        "%ProgramFiles%\Tencent\QQNT\QQ.exe"
        "%ProgramFiles(x86)%\Tencent\QQNT\QQ.exe"
        "%LOCALAPPDATA%\Programs\Tencent\QQNT\QQ.exe"
        "D:\Program Files\Tencent\QQNT\QQ.exe"
        "D:\Tencent\QQNT\QQ.exe"
        "E:\Program Files\Tencent\QQNT\QQ.exe"
        "E:\Tencent\QQNT\QQ.exe"
    ) do (
        if exist %%p (
            set "QQPath=%%~p"
            echo [OK] 在常见路径找到 QQ
        )
    )
)

REM --- 4.4 未找到：给出指引 ---
if not defined QQPath (
    if defined AUTO_MODE (
        echo [错误] 未找到 QQ 客户端（详见 napcat_launch.log）
        echo [%DATE% %TIME%] [错误] 未找到 QQ 客户端 >>"%LOGFILE%"
        exit /b 1
    )
    echo [错误] 未找到 QQ 客户端
    echo.
    echo NapCat 需要本机安装 QQ 客户端才能工作。
    echo.
    echo 请选择一种方式：
    echo   1. 安装 QQ 客户端（官网免费）：
    echo      https://im.qq.com/pcqq
    echo      安装后重新运行本脚本即可自动识别
    echo.
    echo   2. 如果 QQ 已安装但位置特殊：
    echo      把 QQ.exe 拖到本脚本上，会自动记录路径
    echo      或手动在本目录创建 qq_path.txt，内容为 QQ.exe 的完整路径
    echo.
    echo   3. 如果使用便携版 QQ：
    echo      同上，用拖拽或 qq_path.txt 指定
    echo.
    pause
    exit /b 1
)

echo.
echo [信息] QQ 路径: %QQPath%
echo.
if defined AUTO_MODE echo [%DATE% %TIME%] QQ 路径: %QQPath% >>"%LOGFILE%"

REM ============ 5. 生成 loadNapCat.js ============
set "NAPCAT_PATCH_PACKAGE=%cd%\qqnt.json"
set "NAPCAT_LOAD_PATH=%cd%\loadNapCat.js"
set "NAPCAT_INJECT_PATH=%cd%\NapCatWinBootHook.dll"
set "NAPCAT_LAUNCHER_PATH=%cd%\NapCatWinBootMain.exe"
set "NAPCAT_MAIN_PATH=%cd%\napcat.mjs"

REM 生成指向 napcat.mjs 的 file:// URL。
REM
REM 要点（v1.0.5 修复）：
REM  1) 必须用 goto 分行写，不能把 echo (...)() 放进 if (...) 块。
REM     echo 内容里的右括号会提前闭合 if 语句块，导致：
REM       「此时不应有 import(...)」然后脚本直接终止，
REM     表现就是「双击 bat 闪一下就没了 / 日志停在 QQ 路径」。
REM  2) 路径里的中文和空格要做百分号编码，用 cscript 算。

set "NAPCAT_FILE_URL="
set "NAPCAT_GEN_URL_VBS=%TEMP%\napcat_genurl_%RANDOM%.vbs"
> "%NAPCAT_GEN_URL_VBS%" echo p = WScript.Arguments(0)
>>"%NAPCAT_GEN_URL_VBS%" echo Do While InStr(p, "\") ^> 0
>>"%NAPCAT_GEN_URL_VBS%" echo   p = Replace(p, "\", "/")
>>"%NAPCAT_GEN_URL_VBS%" echo Loop
>>"%NAPCAT_GEN_URL_VBS%" echo out = ""
>>"%NAPCAT_GEN_URL_VBS%" echo For i = 1 To Len(p)
>>"%NAPCAT_GEN_URL_VBS%" echo   ch = Mid(p, i, 1)
>>"%NAPCAT_GEN_URL_VBS%" echo   code = AscW(ch)
>>"%NAPCAT_GEN_URL_VBS%" echo   If code ^< 0 Then code = code + 65536
>>"%NAPCAT_GEN_URL_VBS%" echo   If code ^> 127 Then
>>"%NAPCAT_GEN_URL_VBS%" echo     out = out ^& Utf8Pct(ch)
>>"%NAPCAT_GEN_URL_VBS%" echo   ElseIf InStr(" %#?", ch) ^> 0 Then
>>"%NAPCAT_GEN_URL_VBS%" echo     out = out ^& "%%" ^& Right("0" ^& Hex(Asc(ch)), 2)
>>"%NAPCAT_GEN_URL_VBS%" echo   Else
>>"%NAPCAT_GEN_URL_VBS%" echo     out = out ^& ch
>>"%NAPCAT_GEN_URL_VBS%" echo   End If
>>"%NAPCAT_GEN_URL_VBS%" echo Next
>>"%NAPCAT_GEN_URL_VBS%" echo WScript.Echo "file:///" ^& out
>>"%NAPCAT_GEN_URL_VBS%" echo Function Utf8Pct(s)
>>"%NAPCAT_GEN_URL_VBS%" echo   Dim st, b, k, r
>>"%NAPCAT_GEN_URL_VBS%" echo   Set st = CreateObject("ADODB.Stream")
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Type = 2
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Charset = "utf-8"
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Open
>>"%NAPCAT_GEN_URL_VBS%" echo   st.WriteText s
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Position = 0
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Type = 1
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Position = 3
>>"%NAPCAT_GEN_URL_VBS%" echo   b = st.Read
>>"%NAPCAT_GEN_URL_VBS%" echo   st.Close
>>"%NAPCAT_GEN_URL_VBS%" echo   r = ""
>>"%NAPCAT_GEN_URL_VBS%" echo   For k = 1 To LenB(b)
>>"%NAPCAT_GEN_URL_VBS%" echo     r = r ^& "%%" ^& Right("0" ^& Hex(AscB(MidB(b, k, 1))), 2)
>>"%NAPCAT_GEN_URL_VBS%" echo   Next
>>"%NAPCAT_GEN_URL_VBS%" echo   Utf8Pct = r
>>"%NAPCAT_GEN_URL_VBS%" echo End Function

for /f "usebackq delims=" %%u in (`cscript //nologo "%NAPCAT_GEN_URL_VBS%" "%NAPCAT_MAIN_PATH%" 2^>nul`) do set "NAPCAT_FILE_URL=%%u"
del "%NAPCAT_GEN_URL_VBS%" >nul 2>&1

if not defined NAPCAT_FILE_URL goto NAPCAT_GEN_FALLBACK

echo (async () =^> {await import("%NAPCAT_FILE_URL%")})() > "%NAPCAT_LOAD_PATH%"
echo [信息] 注入脚本已生成
if defined AUTO_MODE echo [%DATE% %TIME%] 注入脚本: %NAPCAT_FILE_URL% >>"%LOGFILE%"
goto NAPCAT_GEN_DONE

:NAPCAT_GEN_FALLBACK
echo [警告] URL 编码失败，回退到简单路径拼接
set "NAPCAT_MAIN_PATH_FWD=%NAPCAT_MAIN_PATH:\=/%"
echo (async () =^> {await import("file:///%NAPCAT_MAIN_PATH_FWD%")})() > "%NAPCAT_LOAD_PATH%"
if defined AUTO_MODE echo [%DATE% %TIME%] [警告] URL 编码失败，已回退 >>"%LOGFILE%"

:NAPCAT_GEN_DONE

REM ============ 6. 注入启动 ============
echo [启动] 正在注入 QQ 客户端...
echo.
echo ============================================
echo   首次使用说明：
echo   1. 会弹出 QQ 登录窗口，用手机 QQ 扫码登录
echo   2. 登录后本窗口会显示 WebUI 地址（含 token）
echo   3. 登录态会保存在本机，下次可快速登录
echo ============================================
echo.
if defined AUTO_MODE echo [%DATE% %TIME%] 开始注入 QQ >>"%LOGFILE%"

"%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%" %PASS_ARGS%
set "EXITCODE=%ERRORLEVEL%"

echo.
echo [结束] NapCat 已退出（退出码 %EXITCODE%）
if defined AUTO_MODE (
    echo [%DATE% %TIME%] NapCat 已退出，退出码 %EXITCODE% >>"%LOGFILE%"
    exit /b %EXITCODE%
)

goto :NAPCAT_RUN_END

:QRY_REG
for /f "tokens=2*" %%a in ('reg query %~1 /v "UninstallString" 2^>nul') do set "RetString=%%~b"
if not defined RetString goto :eof
for %%a in ("!RetString!") do set "RetDir=%%~dpa"
if exist "!RetDir!QQ.exe" (
    set "QQPath=!RetDir!QQ.exe"
    echo [OK] 从注册表找到 QQ
)
goto :eof

:NAPCAT_RUN_END
pause