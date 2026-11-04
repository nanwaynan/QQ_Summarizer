"""QQ 客户端版本检测

NapCat 通过注入本机 QQ 客户端工作，所以 QQ 的版本直接影响可用性：
- 版本太低 → NapCat 的偏移表里没有对应条目，可能无法工作
- 版本太新 → 同样可能不在支持列表内

本模块负责探测 QQ 安装位置与版本，并给出兼容性判断。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# NapCat 官方要求的最低 build（来自 napcat.mjs 的 requireMinNTQQBuild）
MIN_BUILD = 40768

# 已知可用的版本区间（参考 NapCat 内置偏移表）
# 说明：这只是「推荐」信息，实际兼容性由 NapCat 的偏移表决定
RECOMMENDED_VERSIONS = '9.9.25 及以上'

QQ_DOWNLOAD_URL = 'https://im.qq.com/pcqq'

# 常见安装路径（相对环境变量 + 全盘符兜底）
# 这只是候选之一，真正靠 find_qq_exe() 的多路探测：
# qq_path.txt → 环境变量 → 运行中进程 → 注册表三视图 → 开始菜单 → 这里 → 全盘浅扫
_COMMON_ENV_PATHS = (
    r'%ProgramFiles%\Tencent\QQNT\QQ.exe',
    r'%ProgramFiles(x86)%\Tencent\QQNT\QQ.exe',
    r'%LOCALAPPDATA%\Programs\Tencent\QQNT\QQ.exe',
    r'%APPDATA%\Tencent\QQNT\QQ.exe',
    r'%ProgramFiles%\QQNT\QQ.exe',
)

_TAIL_PATHS = (
    r'Tencent\QQNT\QQ.exe',
    r'Program Files\Tencent\QQNT\QQ.exe',
    r'Program Files (x86)\Tencent\QQNT\QQ.exe',
    r'Apps\QQNT\QQ.exe',
    r'Software\QQNT\QQ.exe',
    r'QQNT\QQ.exe',
)

_SEP = "\\"   # 单个反斜杠

COMMON_PATHS = _COMMON_ENV_PATHS + tuple(
    drv + ":" + _SEP + sub
    for drv in ('C', 'D', 'E', 'F', 'G')
    for sub in _TAIL_PATHS
)

def _expand(p: str) -> Path:
    import os
    return Path(os.path.expandvars(p))


def find_qq_exe(deep: bool = True) -> Path | None:
    """探测 QQ.exe 路径。

    按「可靠度从高到低」多路搜索，任一路命中即返回：

    1. NapCat 目录下的 `qq_path.txt`（用户手动指定 / 拖拽记录，最可靠）
    2. 环境变量 `QQ_PATH`（便于脚本化部署）
    3. 运行中的 QQ.exe 进程（QQ 开着就一定能拿到真实路径）
    4. 注册表：64 位项 / WOW6432Node / HKCU，逐个试
    5. 开始菜单快捷方式（覆盖「装在任意位置」的绝大多数情况）
    6. 常见安装路径（含所有盘符 + 便携版目录名）
    7. 全盘浅层扫描 `*\\QQNT\\QQ.exe`（`deep=True` 时，较慢但兜底）

    Args:
        deep: 是否做全盘浅扫描。启动流程里用 True；界面刷新等高频场景可用 False。
    """
    # 1. 用户手动指定（最高优先级）
    p = _from_qq_path_txt()
    if p:
        return p

    # 2. 环境变量
    import os as _os
    env = (_os.environ.get('QQ_PATH') or '').strip()
    if env:
        q = Path(env.strip('"'))
        if q.is_file() and q.name.lower() == 'qq.exe':
            return q

    # 3. 运行中的进程（最真实）
    p = _from_process()
    if p:
        return p

    # 4. 注册表多视图
    p = _from_registry()
    if p:
        return p

    # 5. 开始菜单快捷方式
    p = _from_start_menu()
    if p:
        return p

    # 6. 常见安装路径（含全盘符）
    for cand in COMMON_PATHS:
        q = _expand(cand)
        if q.exists():
            return q

    # 7. 全盘浅扫（最后兜底）
    if deep:
        p = _scan_disks()
        if p:
            return p

    return None


def _from_qq_path_txt() -> Path | None:
    """读 napcat_green/qq_path.txt（用户手动指定或拖拽生成的）。"""
    try:
        d = _guess_napcat_dir()
    except Exception:
        return None
    if not d:
        return None
    f = Path(d) / 'qq_path.txt'
    if not f.exists():
        return None
    try:
        for line in f.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            q = Path(line)
            if q.is_file() and q.name.lower() == 'qq.exe':
                return q
    except OSError:
        pass
    return None


def _from_process() -> Path | None:
    """从正在运行的 QQ.exe 进程取真实路径。

    QQ 开着时这是最准确的来源 —— 不管装在哪，进程路径就是答案。
    """
    try:
        r = subprocess.run(
            ['wmic', 'process', 'where', "name='QQ.exe'", 'get',
             'ExecutablePath', '/format:list'],
            capture_output=True, text=True, timeout=20,
            encoding='gbk', errors='replace')
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith('executablepath='):
                v = line.split('=', 1)[1].strip().strip('"')
                if v:
                    q = Path(v)
                    if q.is_file():
                        return q
    except Exception:
        pass

    # wmic 在新系统上被移除，退回 PowerShell
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "(Get-Process QQ -ErrorAction SilentlyContinue | "
             "Select-Object -First 1 -ExpandProperty Path)"],
            capture_output=True, text=True, timeout=20,
            encoding='utf-8', errors='replace')
        v = (r.stdout or '').strip()
        if v:
            q = Path(v)
            if q.is_file() and q.name.lower() == 'qq.exe':
                return q
    except Exception:
        pass
    return None


def _from_start_menu() -> Path | None:
    """解析开始菜单里的 QQ 快捷方式（.lnk）。

    覆盖「装在任意位置」的绝大多数情况：用户在开始菜单点得到，说明快捷方式
    里有真实路径。用 PowerShell 的 WScript.Shell 读取 TargetPath。
    """
    import os as _os

    roots = []
    for env in ('ProgramData', 'APPDATA'):
        base = _os.environ.get(env)
        if base:
            roots.append(Path(base) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs')

    if not roots:
        return None

    # 用 PowerShell 批量解析，避免逐文件起进程
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$sh=New-Object -ComObject WScript.Shell;"
        "$dirs=@("
        + ','.join("'%s'" % str(r).replace("'", "''") for r in roots)
        + ");"
        "foreach($d in $dirs){"
        " Get-ChildItem -Path $d -Filter '*QQ*.lnk' -Recurse | ForEach-Object {"
        "  $t=$sh.CreateShortcut($_.FullName).TargetPath;"
        "  if($t -and (Test-Path $t)){ Write-Output $t }"
        " }"
        "}"
    )
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace')
        for line in (r.stdout or '').splitlines():
            v = line.strip()
            if v.lower().endswith('qq.exe'):
                q = Path(v)
                if q.is_file():
                    return q
    except Exception:
        pass
    return None


def _scan_disks() -> Path | None:
    """全盘浅层扫描 `QQNT\\QQ.exe`（兜底）。

    只扫两层深度（`X:\\*\\QQNT\\QQ.exe`、`X:\\*\\*\\QQNT\\QQ.exe`），
    避免遍历整块盘。普通机械盘约 3-10 秒，SSD 更快。
    """
    import string

    try:
        drives = [f'{d}:\\' for d in string.ascii_uppercase if Path(f'{d}:\\').exists()]
    except Exception:
        return None

    # 常见深层安装目录前缀，优先扫这些（快）
    guesses = [
        'Tencent\\QQNT\\QQ.exe',
        'Program Files\\Tencent\\QQNT\\QQ.exe',
        'Program Files (x86)\\Tencent\\QQNT\\QQ.exe',
        'Apps\\QQNT\\QQ.exe',
        'Software\\QQNT\\QQ.exe',
    ]
    for drv in drives:
        for g in guesses:
            q = Path(drv) / g
            try:
                if q.is_file():
                    return q
            except OSError:
                continue

    # 一层通配
    for drv in drives:
        try:
            for sub in Path(drv).iterdir():
                if not sub.is_dir():
                    continue
                q = sub / 'QQNT' / 'QQ.exe'
                try:
                    if q.is_file():
                        return q
                except OSError:
                    continue
        except OSError:
            continue

    # 两层通配
    for drv in drives:
        try:
            for sub in Path(drv).iterdir():
                if not sub.is_dir():
                    continue
                try:
                    for sub2 in sub.iterdir():
                        if not sub2.is_dir():
                            continue
                        q = sub2 / 'QQNT' / 'QQ.exe'
                        try:
                            if q.is_file():
                                return q
                        except OSError:
                            continue
                except OSError:
                    continue
        except OSError:
            continue
    return None


def _from_registry() -> Path | None:
    """从注册表读 QQ 安装目录。

    依次尝试三个视图：
    - HKLM 64 位
    - HKLM WOW6432Node（32 位程序视角）
    - HKCU 当前用户（用户级安装）

    以前只查 WOW6432Node，在 64 位系统 / 用户级安装的情况下会漏掉。
    """
    KEYS = (
        r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ',
        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ',
    )
    for key in KEYS:
        for root in ('HKLM', 'HKCU'):
            try:
                r = subprocess.run(
                    ['reg', 'query', f'{root}\\{key}', '/v', 'UninstallString'],
                    capture_output=True, text=True, timeout=15,
                    encoding='gbk', errors='replace')
            except Exception:
                continue

            m = re.search(r'REG_SZ\s+(.+)', r.stdout or '')
            if not m:
                continue
            uninstall = m.group(1).strip().strip('"')
            qq_dir = Path(uninstall).parent
            exe = qq_dir / 'QQ.exe'
            try:
                if exe.is_file():
                    return exe
            except OSError:
                continue

            # UninstallString 有时指向子目录，往上找一层
            for parent in (qq_dir.parent, qq_dir.parent.parent):
                e2 = parent / 'QQ.exe'
                try:
                    if e2.is_file():
                        return e2
                except OSError:
                    continue
    return None


def _guess_napcat_dir() -> Path | None:
    """猜测 NapCat 目录（不依赖 config，避免循环导入）。

    探测顺序：程序同目录的 napcat_green/napcat → C:/NapCat/shell*
    """
    import sys as _sys

    bases: list[Path] = []
    if getattr(_sys, 'frozen', False):
        bases.append(Path(_sys.executable).parent)
    else:
        bases.append(Path(__file__).resolve().parent.parent.parent)
    bases.append(Path('C:/NapCat'))
    bases.append(Path('D:/NapCat'))

    def _ok(d: Path) -> bool:
        return (d / 'napcat.mjs').exists()

    # 1. 程序同目录下的候选
    for base in bases[:1]:
        for name in ('napcat_green', 'napcat', 'NapCat'):
            cand = base / name
            if _ok(cand):
                return cand

    # 2. C:/NapCat/shell* 之类的安装目录
    for root in bases[1:]:
        if not root.is_dir():
            continue
        try:
            for sub in sorted(root.iterdir(), reverse=True):
                if sub.is_dir() and _ok(sub):
                    return sub
        except OSError:
            continue
    return None


def detect_qq(install_path: Path | None = None,
              napcat_dir: Path | None = None,
              deep: bool = False) -> dict:
    """检测 QQ 客户端安装与版本。

    Args:
        install_path: 指定 QQ.exe 路径；不传则自动探测
        napcat_dir: 指定 NapCat 目录（用于查偏移表）；不传则从配置读
        deep: 是否做全盘浅扫。界面刷新等高频场景用 False（默认），
              启动流程里用 True 以确保能找到装在任意位置的 QQ。

    Returns:
        {
            'installed': bool,
            'path': str,           # QQ.exe 完整路径
            'install_dir': str,
            'version': str,        # 如 '9.9.36-53644'
            'build': int,          # 53644
            'compatible': bool,    # 是否满足最低要求
            'message': str,        # 给用户看的一句话说明
        }
    """
    out = {
        'installed': False, 'path': '', 'install_dir': '',
        'version': '', 'build': 0, 'compatible': False, 'message': '',
    }

    # 解析 NapCat 目录（供偏移表检查用）
    if napcat_dir is None:
        napcat_dir = _guess_napcat_dir()

    exe = install_path or find_qq_exe(deep=deep)
    if not exe or not Path(exe).exists():
        out['message'] = ('未检测到 QQ 客户端。\n'
                          f'请先安装 QQ：{QQ_DOWNLOAD_URL}')
        return out

    exe = Path(exe)
    out['installed'] = True
    out['path'] = str(exe)
    out['install_dir'] = str(exe.parent)

    # 读版本：优先 versions/config.json 的 curVersion（最准）
    version = _read_version(exe.parent)
    if version:
        out['version'] = version
        m = re.search(r'-(\d+)$', version)
        if m:
            out['build'] = int(m.group(1))

    # 兼容性判断
    if out['build']:
        if out['build'] < MIN_BUILD:
            out['compatible'] = False
            out['message'] = (f'QQ 版本过低（{version}），'
                              f'建议升级到 {RECOMMENDED_VERSIONS}')
        else:
            # 关键：必须精确命中偏移表才算兼容。
            # 以前这里「不在表里也算 compatible=True」，导致程序明知版本不支持
            # 还继续注入 —— QQ 会报「文件已损坏」然后崩溃。
            exact = _is_in_napcat_table(version, napcat_dir)
            if exact:
                out['compatible'] = True
                out['message'] = f'QQ {version} ✓ 已在 NapCat 支持列表内'
            else:
                out['compatible'] = False
                highest = supported_versions(napcat_dir)
                top = highest[-1] if highest else ''
                out['message'] = (
                    f'QQ {version} 不在 NapCat 支持列表内'
                    + (f'（支持最高 {top}）' if top else '')
                    + '，注入会失败')
    else:
        out['compatible'] = False
        out['message'] = ('无法读取 QQ 版本信息。\n'
                          f'建议使用 {RECOMMENDED_VERSIONS} 的官方版本')

    return out


def _read_version(install_dir: Path) -> str:
    """从 QQ 安装目录读版本号。

    优先 versions/config.json 的 curVersion，
    退化到 versions/ 下的目录名。
    """
    cfg = install_dir / 'versions' / 'config.json'
    if cfg.exists():
        try:
            d = json.loads(cfg.read_text(encoding='utf-8-sig'))
            v = str(d.get('curVersion') or '').strip()
            if v:
                return v
        except Exception:
            pass

    # 退化：看 versions/ 下的目录名
    vdir = install_dir / 'versions'
    if vdir.is_dir():
        cands = []
        for p in vdir.iterdir():
            if p.is_dir() and re.match(r'^\d+\.\d+\.\d+-\d+$', p.name):
                cands.append(p.name)
        if cands:
            return sorted(cands)[-1]
    return ''


def supported_versions(napcat_dir: Path | None = None) -> list[str]:
    """从 napcat.mjs 的偏移表里提取所有受支持的 QQ 版本。

    NapCat 通过内存偏移注入 QQ，每个 QQ 版本的内存布局都不同，所以它内置
    一张「版本 → 偏移」表。表里没有的版本注入会失败 —— 表现为 QQ 弹
    「文件已损坏，请重新安装QQ」然后自己退出。

    Returns:
        版本号列表，如 ['9.9.25-42744', ..., '9.9.36-53644']（已排序）；查不到返回 []
    """
    if napcat_dir is None:
        try:
            napcat_dir = _guess_napcat_dir()
        except Exception:
            return []
    if not napcat_dir:
        return []

    mjs = Path(napcat_dir) / 'napcat.mjs'
    if not mjs.exists():
        return []

    try:
        text = mjs.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return []

    found = set(re.findall(r'"(\d+\.\d+\.\d+-\d+)(?:-[a-z0-9]+)?"', text))
    return sorted(found, key=_version_sort_key)


def _version_sort_key(v: str) -> tuple:
    """把 '9.9.36-53644' 变成可排序的元组。"""
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)-(\d+)$', v)
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(x) for x in m.groups())


def qq_version_status(install_path: Path | None = None,
                      napcat_dir: Path | None = None,
                      deep: bool = True) -> dict:
    """判断当前 QQ 版本能否被内置 NapCat 精确支持。

    这是「QQ 弹文件已损坏」的根因判定：NapCat 的偏移表里没有对应版本时，
    注入会破坏 QQ 内存，QQ 自检失败后退出。所以启动前必须拦下来。

    Returns:
        {
          'installed': bool,
          'version': str,
          'build': int,
          'supported': bool,     # 是否在偏移表内（决定能不能注入）
          'table_size': int,     # 偏移表里有多少个版本
          'max_supported': str,  # 支持的最高版本
          'message': str,        # 给用户看的一句话
          'advice': str,         # 怎么办
        }
    """
    out = {
        'installed': False, 'version': '', 'build': 0, 'supported': False,
        'table_size': 0, 'max_supported': '', 'message': '', 'advice': '',
    }

    info = detect_qq(install_path, napcat_dir, deep=deep)
    out['installed'] = info['installed']
    out['version'] = info['version']
    out['build'] = info['build']

    if not info['installed']:
        out['message'] = '未检测到 QQ 客户端'
        out['advice'] = f'请先安装 QQ：{QQ_DOWNLOAD_URL}'
        return out

    if not info['version']:
        out['message'] = '无法读取 QQ 版本'
        out['advice'] = '请确认 QQ 安装完整，或重装官方版本'
        return out

    table = supported_versions(napcat_dir)
    out['table_size'] = len(table)
    out['max_supported'] = table[-1] if table else ''

    if info['version'] in table:
        out['supported'] = True
        out['message'] = f'QQ {info["version"]} 在 NapCat 支持列表内'
        return out

    # 不在表里 —— 这就是会导致 QQ 崩溃的情况
    out['supported'] = False
    if not table:
        out['message'] = f'无法读取 NapCat 的支持列表（QQ {info["version"]}）'
        out['advice'] = ('请确认 napcat_green 目录完整。'
                         '如果刚升级过 QQ，建议回退到 9.9.36-53644。')
        return out

    newer = _version_sort_key(info['version']) > _version_sort_key(table[-1])
    if newer:
        out['message'] = (f'QQ {info["version"]} 比内置 NapCat 支持的'
                          f'最高版本（{table[-1]}）更新')
    else:
        out['message'] = (f'QQ {info["version"]} 不在内置 NapCat 的支持列表内'
                          f'（支持最高 {table[-1]}）')

    out['advice'] = (
        '这个版本注入会失败，QQ 会弹出「文件已损坏」并退出。\n'
        '两种解决办法（任选其一）：\n'
        f'  1. 把 QQ 换成 {table[-1]}（推荐，已在支持列表内）\n'
        '     官网历史版本：https://im.qq.com/pcqq/index.shtml\n'
        '  2. 换用更新的 NapCat 内核（支持你当前 QQ 版本的）\n'
        '     覆盖到 napcat_green 目录即可'
    )
    return out


def _is_in_napcat_table(version: str, napcat_dir: Path | None = None) -> bool:
    """检查版本是否出现在 NapCat 的偏移表里（说明被精确支持）。

    NapCat 的偏移表形如 `"9.9.36-53644-x64": { send: "...", recv: "..." }`
    （在 napcat.mjs 约 13% 位置）。

    Args:
        version: QQ 版本号，如 '9.9.36-53644'
        napcat_dir: NapCat 目录；不传则自动从配置读
    """
    if not version:
        return False

    if napcat_dir is None:
        try:
            from .config import Config
            cfg = Config.load()
            napcat_dir = Path(str(cfg.get('napcat_dir') or ''))
        except Exception:
            return False

    mjs = Path(napcat_dir) / 'napcat.mjs'
    if not mjs.exists():
        return False

    needles = [f'"{version}-x64"', f'"{version}"']
    try:
        # 文件约 3MB，直接整读比流式分块更可靠（不会有边界截断问题）
        text = mjs.read_text(encoding='utf-8', errors='ignore')
        return any(n in text for n in needles)
    except Exception:
        return False


def napcat_version() -> str:
    """读 NapCat 自身版本。

    优先级：
    1. OneBot 的 get_version_info（需要服务已在运行）
    2. napcat.mjs 里的 `NapCat.Core Version: x.y.z` 字样
    3. package.json 的 version（可能是 0.0.1 占位值，不采用）
    """
    # 1. 从运行中的服务拿（最准）
    try:
        from .napcat import OneBotClient
        from .config import Config
        cfg = Config.load()
        info = OneBotClient(cfg).version_info()
        v = str(info.get('app_version') or '').strip()
        if v:
            return v
    except Exception:
        pass

    # 2. 从 napcat.mjs 内容里找
    try:
        from .config import Config
        cfg = Config.load()
        mjs = Path(str(cfg.get('napcat_dir') or '')) / 'napcat.mjs'
        if mjs.exists():
            with mjs.open('r', encoding='utf-8', errors='ignore') as f:
                for _ in range(20):     # 只看前若干 MB
                    chunk = f.read(512 * 1024)
                    if not chunk:
                        break
                    m = re.search(r'NapCat\.Core Version:\s*([\d.]+)', chunk)
                    if m:
                        return m.group(1)
    except Exception:
        pass

    return ''
