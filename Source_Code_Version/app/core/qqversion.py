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

# 常见安装路径（相对环境变量）
COMMON_PATHS = (
    r'%ProgramFiles%\Tencent\QQNT\QQ.exe',
    r'%ProgramFiles(x86)%\Tencent\QQNT\QQ.exe',
    r'%LOCALAPPDATA%\Programs\Tencent\QQNT\QQ.exe',
    r'D:\Program Files\Tencent\QQNT\QQ.exe',
    r'D:\Tencent\QQNT\QQ.exe',
    r'E:\Program Files\Tencent\QQNT\QQ.exe',
)


def _expand(p: str) -> Path:
    import os
    return Path(os.path.expandvars(p))


def find_qq_exe() -> Path | None:
    """探测 QQ.exe 路径。

    优先级：注册表 → 常见路径 → NapCat 目录里的 qq_path.txt
    """
    # 1. 注册表（官方 NapCat 的探测方式）
    p = _from_registry()
    if p and p.exists():
        return p

    # 2. 常见安装路径
    for cand in COMMON_PATHS:
        q = _expand(cand)
        if q.exists():
            return q

    # 3. NapCat 绿色版目录里的手动指定文件
    try:
        from .config import Config  # 延迟导入避免循环
    except ImportError:
        pass

    return None


def _from_registry() -> Path | None:
    """从注册表读 QQ 安装目录（WOW6432Node 视图，与 NapCat 官方一致）。"""
    try:
        r = subprocess.run(
            ['reg', 'query',
             r'HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ',
             '/v', 'UninstallString'],
            capture_output=True, text=True, timeout=15,
            encoding='gbk', errors='replace')
    except Exception:
        return None

    m = re.search(r'REG_SZ\s+(.+)', r.stdout)
    if not m:
        return None
    uninstall = m.group(1).strip().strip('"')
    qq_dir = Path(uninstall).parent
    exe = qq_dir / 'QQ.exe'
    return exe if exe.exists() else None


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
              napcat_dir: Path | None = None) -> dict:
    """检测 QQ 客户端安装与版本。

    Args:
        install_path: 指定 QQ.exe 路径；不传则自动探测
        napcat_dir: 指定 NapCat 目录（用于查偏移表）；不传则从配置读

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

    exe = install_path or find_qq_exe()
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
        if out['build'] >= MIN_BUILD:
            # 检查是否在 NapCat 的偏移表里（能查到就是精确支持）
            exact = _is_in_napcat_table(version, napcat_dir)
            if exact:
                out['compatible'] = True
                out['message'] = f'QQ {version} ✓ 已在 NapCat 支持列表内'
            else:
                out['compatible'] = True
                out['message'] = (f'QQ {version} ✓ 高于最低要求 '
                                  f'（{MIN_BUILD}），但可能不在精确支持列表内')
        else:
            out['compatible'] = False
            out['message'] = (f'QQ 版本过低（{version}），'
                              f'建议升级到 {RECOMMENDED_VERSIONS}')
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
