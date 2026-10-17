"""生成 NapCat 精简绿色版

从现有 NapCat 安装复制出可分发的精简版本：
- 删除其他平台的 native addon（linux/darwin，约 88MB）
- 删除运行时产物与隐私数据（logs、guild1.db、账号配置）
- 只保留 Windows x64 运行必需的文件

用法：
    python make_napcat_green.py <源目录> <目标目录>

例：
    python make_napcat_green.py C:\\NapCat\\shell41838 C:\\QQSummarizer\\exe_version\\napcat_green
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# ---------- 保留/删除规则 ----------

# 顶层必需文件（NapCat 运行不能少）
KEEP_FILES = {
    'napcat.mjs',
    'NapCatWinBootMain.exe',
    'NapCatWinBootHook.dll',
    'qqnt.json',
    'package.json',
    'loadNapCat.js',
    'conout-wiJ7YKRd.js',
    'KillQQ.bat',
    'launch_hidden.vbs',
}

# 顶层必需目录
KEEP_DIRS = {
    'node_modules',
    'static',
    'worker',
    'native',
}

# 需要删除的平台子目录（Windows 上不会加载）
DROP_NATIVE_PATTERNS = (
    '.linux.', 'linux.', '/linux/',
    '.darwin.', 'darwin.',
)

# 隐私与运行时产物（不能分发）
DROP_NAMES = {
    'logs', 'cache', 'guild1.db',
}


def sizeof(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob('*'):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def build_green(src: Path, dst: Path, verbose: bool = True) -> dict:
    """执行精简复制，返回统计信息。"""
    if not src.is_dir():
        raise SystemExit(f'源目录不存在: {src}')

    # 清理目标
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)

    stats = {
        'copied_files': 0,
        'copied_bytes': 0,
        'skipped_native': 0,
        'skipped_skipped_bytes': 0,
        'privacy_removed': [],
    }

    def _log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    # ---- 1. 顶层文件 ----
    for item in src.iterdir():
        name = item.name

        # 隐私/运行时产物：跳过
        if name in DROP_NAMES:
            stats['privacy_removed'].append(name)
            _log(f'  [跳过-隐私] {name}')
            continue

        # 备份文件：跳过
        if '.bak-' in name or name.endswith('.bak'):
            stats['privacy_removed'].append(name)
            continue

        # 可选脚本（保留 KillQQ，其他 launcher 不要）
        if item.is_file():
            if name.endswith('.bat') and name not in KEEP_FILES:
                _log(f'  [跳过-脚本] {name}')
                continue
            if name not in KEEP_FILES and item.suffix in ('.log', '.db'):
                _log(f'  [跳过-产物] {name}')
                continue
            try:
                shutil.copy2(item, dst / name)
                stats['copied_files'] += 1
                stats['copied_bytes'] += item.stat().st_size
            except OSError as e:
                _log(f'  [失败] {name}: {e}')
            continue

        # ---- 2. 目录 ----
        if item.is_dir():
            if name not in KEEP_DIRS:
                _log(f'  [跳过-目录] {name}/')
                stats['privacy_removed'].append(name + '/')
                continue

            if name == 'native':
                n_copied, n_skipped, b_skipped = _copy_native(item, dst / name, _log)
                stats['copied_files'] += n_copied
                stats['skipped_native'] += n_skipped
                stats['skipped_skipped_bytes'] += b_skipped
                continue

            # 普通目录整体复制
            try:
                shutil.copytree(item, dst / name, dirs_exist_ok=True)
                n = sum(1 for _ in (dst / name).rglob('*') if _.is_file())
                stats['copied_files'] += n
                stats['copied_bytes'] += sizeof(dst / name)
            except OSError as e:
                _log(f'  [失败] {name}/: {e}')

    # ---- 3. config 目录：只留干净的模板 ----
    _make_clean_config(dst / 'config', _log)

    # ---- 4. 隐藏提权启动器（缺它就会弹出可见控制台）----
    _write_hidden_launcher(dst, _log)

    return stats


def _copy_native(src: Path, dst: Path, log) -> tuple[int, int, int]:
    """复制 native 目录，跳过非 Windows 平台。"""
    copied = skipped = skipped_bytes = 0
    for p in src.rglob('*'):
        if not p.is_file():
            continue
        rel = str(p.relative_to(src)).replace('\\', '/')
        low = '/' + rel.lower()

        # 跳过 linux / darwin
        if any(pat in low for pat in DROP_NATIVE_PATTERNS):
            skipped += 1
            try:
                skipped_bytes += p.stat().st_size
            except OSError:
                pass
            continue

        # 跳过 win32-arm64（本机是 x64）
        if 'win32-arm64' in low:
            skipped += 1
            try:
                skipped_bytes += p.stat().st_size
            except OSError:
                pass
            continue

        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(p, target)
            copied += 1
        except OSError as e:
            log(f'  [失败] native/{rel}: {e}')

    log(f'  [native] 保留 {copied} 个文件，跳过 {skipped} 个（{human(skipped_bytes)}）')
    return copied, skipped, skipped_bytes


def _make_clean_config(cfg_dir: Path, log) -> None:
    """创建干净的 config 目录（不含任何账号信息）。"""
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # onebot11.json：预配好 HTTP API（总结程序需要 3000 端口）
    import json
    onebot = {
        "network": {
            "httpServers": [{
                "name": "summary-api",
                "enable": True,
                "port": 3000,
                "host": "127.0.0.1",
                "enableCors": True,
                "enableWebsocket": False,
                "messagePostFormat": "array",
                "token": "",
                "debug": False,
            }],
            "httpSseServers": [],
            "httpClients": [],
            "websocketServers": [],
            "websocketClients": [],
        },
        "musicSignUrl": "",
        "enableLocalFile2Url": False,
        "parseMultMsg": False,
    }
    (cfg_dir / 'onebot11.json').write_text(
        json.dumps(onebot, ensure_ascii=False, indent=2), encoding='utf-8')

    # napcat.json：日志落盘而非打印到控制台。
    # 原因：NapCat 内核输出是 UTF-8（还带 ANSI 色彩码），而启动脚本用 chcp 936
    # 的 GBK 控制台，中文会全变成乱码。关掉 consoleLog 后输出进 logs/，
    # 既没有乱码，排查问题也有据可查。
    napcat_cfg = {
        "fileLog": True,
        "consoleLog": False,
        "fileLogLevel": "info",
        "consoleLogLevel": "info",
        "packetBackend": "auto",
        "packetServer": "",
        "o3HookMode": 1,
    }
    (cfg_dir / 'napcat.json').write_text(
        json.dumps(napcat_cfg, ensure_ascii=False, indent=2), encoding='utf-8')

    # webui.json：固定端口与 token（方便用户首次访问面板）
    webui = {
        "host": "127.0.0.1",
        "port": 6099,
        "token": "qsummarizer",
        "loginRate": 3,
        "autoLoginAccount": "",
        "theme": "auto",
        "disableWebUI": False,
        "accessControlMode": "none",
        "ipWhitelist": [],
        "ipBlacklist": [],
        "enableXForwardedFor": False,
    }
    (cfg_dir / 'webui.json').write_text(
        json.dumps(webui, ensure_ascii=False, indent=2), encoding='utf-8')

    log('  [config] 已生成干净模板（不含任何账号信息）')


# 隐藏提权启动器。内容与 exe_version/napcat_green/launch_hidden.vbs 保持一致，
# 放在这里是为了「重新生成绿色包时不丢这个文件」。
HIDDEN_LAUNCHER_VBS = r'''' 隐藏窗口 + 提权 启动器
'
' 为什么需要它：
'   直接 `cmd /c start /min 启动NapCat.bat` 时，/min 和 CREATE_NO_WINDOW 只能
'   压制第一层窗口。注入 QQ 需要管理员权限，bat 内部会再用
'   `powershell Start-Process -Verb RunAs` 拉起提权副本 —— 那一层是以正常大小
'   可见的，于是切换账号/自动启动时会弹出一个「管理员: NapCat 启动器」窗口。
'
'   本脚本用 ShellExecute 的 "runas" 动作 + 窗口模式 0（隐藏），一次调用同时
'   拿到提权与隐藏，不再产生可见窗口。
'
' 用法：
'   wscript.exe //nologo launch_hidden.vbs "<bat路径>" [附加参数...]
'
' 注意：
'   · 仍会弹 UAC 确认框（Windows 机制，无法绕过），点「是」之后不再出现
'     任何控制台窗口。
'   · 窗口是隐藏的，无法应答交互提问，因此未显式传参时自动补 --auto，
'     避免 bat 里的 set /p 或 pause 造成不可见的永久挂起。

Option Explicit

Dim args, count, batPath, i, extra, sh, cmdLine, fso, workDir, hasAuto

Set args = WScript.Arguments
count = args.Count

If count < 1 Then
    WScript.Quit 1
End If

batPath = args(0)

' 收集附加参数，同时判断是否已显式指定自动模式
extra = ""
hasAuto = False
For i = 1 To count - 1
    extra = extra & " " & MaybeQuote(args(i))
    If LCase(args(i)) = "--auto" Then hasAuto = True
Next

' 隐藏窗口下无法应答交互提问，默认走无人值守
If Not hasAuto Then
    extra = extra & " --auto"
End If

cmdLine = "/c " & Quote(batPath) & extra

' 工作目录取 bat 所在目录（提权后默认目录会变成 System32）
workDir = ""
On Error Resume Next
Set fso = CreateObject("Scripting.FileSystemObject")
workDir = fso.GetParentFolderName(batPath)
On Error GoTo 0

On Error Resume Next
Set sh = CreateObject("Shell.Application")
' ShellExecute(文件, 参数, 目录, 动作, 窗口模式)
'   "runas" → 提权；0 → 隐藏窗口
sh.ShellExecute "cmd.exe", cmdLine, workDir, "runas", 0
If Err.Number <> 0 Then
    ' 用户取消 UAC 等情况
    WScript.Quit 2
End If
On Error GoTo 0

WScript.Quit 0

' 给参数加双引号（内部引号翻倍转义）
Function Quote(s)
    Quote = """" & Replace(s, """", """""") & """"
End Function

' 仅在参数含空格时才加引号。
' ShellExecute 传给 cmd 的是纯字符串，cmd 会把带引号的 "--auto" 当成整体；
' 实测无引号的裸参数解析更稳，且 bat 内 %~1 取值不受影响。含空格的路径
' 必须加引号，否则会被拆成多个参数。
Function MaybeQuote(s)
    If InStr(s, " ") > 0 Then
        MaybeQuote = Quote(s)
    Else
        MaybeQuote = s
    End If
End Function
'''


def _write_hidden_launcher(dst: Path, log) -> None:
    """写出隐藏提权启动器（GUI 启动 NapCat 时用它避免弹出可见控制台）。"""
    p = dst / 'launch_hidden.vbs'
    # cscript/wscript 按系统 ANSI 代码页读取 .vbs：UTF-8 BOM 会被当成
    # 「无效字符」直接编译失败，所以存 GBK（简体中文环境即 ANSI）+ CRLF。
    body = HIDDEN_LAUNCHER_VBS.replace('\r\n', '\n').replace('\r', '\n')
    p.write_bytes(body.replace('\n', '\r\n').encode('gbk'))
    log(f'  [launcher] 已生成 {p.name}（隐藏提权启动器）')


def main() -> int:
    ap = argparse.ArgumentParser(description='生成 NapCat 精简绿色版')
    ap.add_argument('src', help='源目录（现有 NapCat 安装）')
    ap.add_argument('dst', help='目标目录（绿色版输出）')
    ap.add_argument('-q', '--quiet', action='store_true')
    args = ap.parse_args()

    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()

    print('=' * 56)
    print(f'源目录: {src}')
    print(f'目标  : {dst}')
    print('=' * 56)

    before = sizeof(src)
    stats = build_green(src, dst, verbose=not args.quiet)
    after = sizeof(dst)

    print()
    print('=' * 56)
    print(f'完成！')
    print(f'  原始体积: {human(before)}')
    print(f'  精简后  : {human(after)}')
    print(f'  节省    : {human(before - after)} '
          f'({(1 - after / before) * 100:.0f}%)')
    print(f'  复制文件: {stats["copied_files"]} 个')
    print(f'  跳过 native: {stats["skipped_native"]} 个 '
          f'({human(stats["skipped_skipped_bytes"])})')
    if stats['privacy_removed']:
        print(f'  移除隐私/产物: {", ".join(stats["privacy_removed"][:8])}')
    print('=' * 56)
    return 0


if __name__ == '__main__':
    sys.exit(main())
