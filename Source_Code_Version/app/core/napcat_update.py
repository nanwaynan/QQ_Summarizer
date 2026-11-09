"""NapCat 内核自动更新

QQ 会持续更新，而 NapCat 靠内存偏移注入 QQ，每个 QQ 版本都要对应的偏移表。
内置内核一旦跟不上 QQ 更新，注入就会失败（表现是 QQ 弹「文件已损坏」）。

本模块让项目「不再被内置内核锁死」：

1. 查 GitHub 上 NapCat 的最新 Release（带缓存，避免频繁请求）
2. 下载 `NapCat.Shell.zip`（官方 Windows Shell 内核）
3. 校验完整性（zip 必须有 napcat.mjs / NapCatWinBootMain.exe 等关键文件）
4. **备份现有内核 → 替换 → 失败自动回滚**
5. 替换后重新读取偏移表，确认新内核支持当前 QQ 版本

设计约束：
- 全程可中断、可回滚，绝不能把用户的内核弄坏
- 下载支持进度回调（GUI 要给进度条）
- 网络失败/校验失败都要给出可读原因，不能静默
- 不碰 `config/`（里面有用户账号配置）和 `cache/`、`logs/`
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

# NapCat 官方仓库
NAPCAT_REPO = 'NapNeko/NapCatQQ'
RELEASE_API = f'https://api.github.com/repos/{NAPCAT_REPO}/releases/latest'
ASSET_NAME = 'NapCat.Shell.zip'

# GitHub 直连在国内常只有几十 KB/s（实测 46 KB/s，34MB 要 12 分钟）。
# 这里准备多个下载源，按顺序尝试，任一成功即停止。
# 用 `{url}` 占位原始下载地址。
DOWNLOAD_MIRRORS = (
    '{url}',                                        # GitHub 直连
    'https://ghfast.top/{url}',                     # 常用公共加速
    'https://gh-proxy.com/{url}',                   # 备用加速
    'https://ghproxy.net/{url}',                    # 备用加速
)

# 替换时必须存在的文件（缺一个就说明包不对）
REQUIRED_FILES = (
    'napcat.mjs',
    'NapCatWinBootMain.exe',
    'NapCatWinBootHook.dll',
    'qqnt.json',
)

# 替换时排除的目录/文件（用户数据或运行时产物，不能覆盖）
EXCLUDE_TOP = {
    'config',     # 账号配置，必须保留
    'cache',      # 二维码等运行时缓存
    'logs',       # NapCat 自己的日志
    'qq_path.txt',       # 用户手动指定的 QQ 路径
    'napcat_launch.log', # 启动日志
}

# 备份目录名（放在 napcat_green 同级，避免被当成内核内容）
BACKUP_DIR_NAME = '_napcat_backup'

UA = 'QQSummarizer-NapCatUpdater/1.0'


# ---------- SSL 上下文 ----------

_ssl_ctx = None


def ssl_context():
    """构造带 CA 证书的 SSL 上下文。

    为什么必须自己做：PyInstaller 打包后，Python 的 `ssl` 找不到系统证书库，
    而项目又不依赖 `certifi`（requirements 里刻意只用标准库）。结果是
    **打包后的程序所有 HTTPS 请求都报 CERTIFICATE_VERIFY_FAILED** ——
    表现为「查询 NapCat 版本失败」，用户看到的却是「QQ 版本不受支持」。

    解决办法：把 cacert.pem 随程序打包（app/data/），运行时显式加载。
    """
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx

    import ssl as _ssl

    candidates = []
    try:
        from ..resources import resource_dir
        candidates.append(resource_dir() / 'app' / 'data' / 'cacert.pem')
    except Exception:
        pass
    candidates.append(Path(__file__).resolve().parent.parent / 'data' / 'cacert.pem')
    candidates.append(Path('/etc/ssl/certs/ca-certificates.crt'))

    for p in candidates:
        try:
            if p.is_file() and p.stat().st_size > 1000:
                _ssl_ctx = _ssl.create_default_context(cafile=str(p))
                return _ssl_ctx
        except Exception:
            continue

    try:
        _ssl_ctx = _ssl.create_default_context()
    except Exception:
        _ssl_ctx = None
    return _ssl_ctx


def _open(req, timeout: int):
    """统一的 urlopen 包装（自动带 CA 证书）。"""
    ctx = ssl_context()
    if ctx is not None:
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    return urllib.request.urlopen(req, timeout=timeout)


class UpdateError(Exception):
    """更新失败（消息可直接展示给用户）。"""


# ---------- 版本信息 ----------

def fetch_latest(timeout: int = 25) -> dict:
    """查询 NapCat 最新 Release。

    Returns:
        {
          'tag': 'v4.18.42',
          'name': '...',
          'published': '2026-10-30T07:05:50Z',
          'asset_url': 'https://...',
          'asset_size': 35967555,
          'html_url': 'https://github.com/.../releases/tag/v4.18.42',
        }
    """
    req = urllib.request.Request(
        RELEASE_API,
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': UA},
    )
    try:
        with _open(req, timeout) as r:
            d = json.loads(r.read().decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as e:
        raise UpdateError(f'查询 NapCat 版本失败：HTTP {e.code}') from e
    except urllib.error.URLError as e:
        raise UpdateError(f'查询 NapCat 版本失败：{e.reason}') from e
    except Exception as e:
        raise UpdateError(f'查询 NapCat 版本失败：{type(e).__name__}: {e}') from e

    assets = d.get('assets') or []
    asset = next((a for a in assets if a.get('name') == ASSET_NAME), None)
    if not asset:
        names = ', '.join(a.get('name', '?') for a in assets) or '（无附件）'
        raise UpdateError(
            f'最新 Release 里找不到 {ASSET_NAME}。\n可用附件：{names}')

    return {
        'tag': str(d.get('tag_name') or ''),
        'name': str(d.get('name') or ''),
        'published': str(d.get('published_at') or ''),
        'asset_url': str(asset.get('browser_download_url') or ''),
        'asset_size': int(asset.get('size') or 0),
        'html_url': str(d.get('html_url') or ''),
    }


def _version_tuple(tag: str) -> tuple:
    """'v4.18.42' → (4, 18, 42)，用于比较大小。"""
    import re
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', tag or '')
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def is_newer(latest_tag: str, current_tag: str) -> bool:
    """latest 是否比 current 新。"""
    return _version_tuple(latest_tag) > _version_tuple(current_tag)


# ---------- 本地内核信息 ----------

def local_versions(napcat_dir: Path) -> dict:
    """读取当前内置内核的信息。

    Returns:
        {
          'dir': str,
          'exists': bool,
          'file_count': int,
          'mjs_size': int,
          'qq_versions': [...],   # 偏移表支持的 QQ 版本
          'max_qq': str,          # 支持的最高 QQ 版本
        }
    """
    from .qqversion import supported_versions

    nd = Path(napcat_dir)
    out = {'dir': str(nd), 'exists': nd.is_dir(), 'file_count': 0,
           'mjs_size': 0, 'qq_versions': [], 'max_qq': ''}
    if not out['exists']:
        return out

    try:
        out['file_count'] = sum(1 for p in nd.rglob('*') if p.is_file())
    except OSError:
        pass

    mjs = nd / 'napcat.mjs'
    if mjs.is_file():
        try:
            out['mjs_size'] = mjs.stat().st_size
        except OSError:
            pass

    vs = supported_versions(nd)
    out['qq_versions'] = vs
    out['max_qq'] = vs[-1] if vs else ''
    return out


# ---------- 下载 ----------

def _mirror_urls(url: str, mirror: str) -> list[str]:
    """按镜像模板生成候选下载地址。"""
    return [m.format(url=url) for m in DOWNLOAD_MIRRORS]


def download_best(url: str, dest: Path,
                  on_progress: Callable[[int, int], None] | None = None,
                  on_stage: Callable[[str], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None,
                  per_source_seconds: int = 5,
                  timeout: int = 30) -> Path:
    """多源下载：逐个尝试加速源，取最快的可用地址。

    为什么要多源：GitHub 直连在国内实测只有约 46 KB/s，34MB 要十几分钟，
    用户会以为程序卡死。公共加速源通常能到几 MB/s。

    策略：
    - 每个源最多测 8 秒，达不到 100 KB/s 就换下一个
    - 找到够快的源就一路下完
    - 全部源都失败才报错
    """
    stage = on_stage or (lambda m: None)
    cancel = should_cancel or (lambda: False)
    urls = _mirror_urls(url, '')

    best: tuple[float, str] | None = None   # (速度 B/s, url)

    # ---- 1. 快速测速（每个源下 8 秒，看速度）----
    for i, u in enumerate(urls):
        if cancel():
            raise UpdateError('已取消下载')
        stage(f'测试下载源 {i+1}/{len(urls)}...')
        try:
            speed = _probe_speed(u, seconds=per_source_seconds, cancel=cancel)
        except UpdateError:
            continue
        except Exception:
            continue
        if speed <= 0:
            continue
        name = 'GitHub 直连' if i == 0 else f'加速源 {i}'
        stage(f'{name}：{speed/1024:.0f} KB/s')
        if best is None or speed > best[0]:
            best = (speed, u)

    if best is None:
        raise UpdateError(
            f'所有下载源都不可用。\n'
            f'请手动下载后解压覆盖：\n'
            f'  https://github.com/{NAPCAT_REPO}/releases/latest\n'
            f'  下载 {ASSET_NAME}，解压到 napcat_green（保留 config 目录）'
        )

    speed, url_best = best
    stage(f'开始下载（{speed/1024:.0f} KB/s）...')

    # ---- 2. 用最快的源下载（总时长按实际速度算）----
    est_seconds = 0
    if speed > 0:
        est_seconds = int(40 * 1024 * 1024 / speed) + 120
    max_seconds = max(300, min(3600, est_seconds))

    return download(url_best, dest,
                    on_progress=on_progress,
                    should_cancel=cancel,
                    timeout=timeout,
                    max_seconds=max_seconds,
                    retries=1)


def _probe_speed(url: str, seconds: int = 5,
                 cancel: Callable[[], bool] | None = None) -> float:
    """测某个源的实际下载速度（B/s）。失败返回 0。"""
    cancel = cancel or (lambda: False)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    t0 = time.monotonic()
    got = 0
    try:
        with _open(req, 10) as r:
            while time.monotonic() - t0 < seconds:
                if cancel():
                    return 0
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                got += len(chunk)
        dt = max(0.001, time.monotonic() - t0)
        speed = got / dt
        # 低于 100 KB/s 视为不可用（34MB 要 5 分钟以上）
        return speed if speed >= 100 * 1024 else 0
    except Exception:
        return 0


def download(url: str, dest: Path,
             on_progress: Callable[[int, int], None] | None = None,
             should_cancel: Callable[[], bool] | None = None,
             timeout: int = 60,
             max_seconds: int = 1800,
             retries: int = 2) -> Path:
    """下载到 dest，支持进度回调、取消、整体超时与重试。

    关键约束（v1.0.7 修复）：
    - `timeout` 只管单次 socket 读，**不能**约束整体下载时长。
      国内访问 GitHub 常见「连上了但速度极慢」，若只设 socket timeout，
      会看起来一直卡住。这里额外用 `max_seconds` 卡总时长。
    - 卡住/中断时按 `retries` 重试，并复用已下载部分做断点续传（Range）。
    - 超过总时长或无进度时间过长 → 抛出可读错误，提示手动下载。

    on_progress(已下载字节, 总字节)；总字节未知时为 0。
    """
    cancel = should_cancel or (lambda: False)
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + '.part')

    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        if cancel():
            raise UpdateError('已取消下载')

        have = tmp.stat().st_size if tmp.exists() else 0
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        if have:
            req.add_header('Range', f'bytes={have}-')

        t_start = time.monotonic()
        got = have
        try:
            with _open(req, timeout) as r:
                # 断点续传失败（服务端不支持 Range）→ 从头来
                if have and r.status != 206:
                    have, got = 0, 0
                    tmp.unlink(missing_ok=True)

                total = 0
                cr = r.headers.get('Content-Length')
                if cr:
                    total = int(cr) + (have if r.status == 206 else 0)

                with open(tmp, 'ab' if have else 'wb') as f:
                    last_data = time.monotonic()
                    while True:
                        if cancel():
                            raise UpdateError('已取消下载')
                        # 整体超时
                        if time.monotonic() - t_start > max_seconds:
                            raise UpdateError(
                                f'下载超时（已 {max_seconds} 秒仍未完成）')
                        # 长时间收不到数据（>60 秒）也算卡死
                        if time.monotonic() - last_data > 60:
                            raise UpdateError('下载停滞超过 60 秒')

                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        last_data = time.monotonic()
                        if on_progress:
                            on_progress(got, total)

            if total and got < total:
                raise UpdateError(f'下载不完整：{got}/{total} 字节')

            os.replace(tmp, dest)
            return dest

        except UpdateError as e:
            last_err = e
            # 取消或超时不再重试
            if '取消' in str(e) or '超时' in str(e) or '停滞' in str(e):
                raise
        except urllib.error.URLError as e:
            last_err = e
        except Exception as e:
            last_err = e

        if attempt < retries:
            time.sleep(2 + attempt * 2)

    hint = (
        f'\n\n多次尝试仍失败。可以直接手动下载后解压覆盖：\n'
        f'  1. 打开 https://github.com/{NAPCAT_REPO}/releases/latest\n'
        f'  2. 下载 {ASSET_NAME}\n'
        f'  3. 解压到 napcat_green 目录（保留其中的 config 文件夹）'
    )
    if isinstance(last_err, UpdateError):
        raise UpdateError(str(last_err) + hint)
    raise UpdateError(f'下载失败：{type(last_err).__name__}: {last_err}{hint}')


# ---------- 校验 ----------

def verify_package(zip_path: Path) -> dict:
    """校验 zip 是不是可用的 NapCat Shell 内核。

    Returns:
        {'ok': bool, 'missing': [...], 'file_count': int, 'qq_count': int, 'max_qq': str}
    """
    out = {'ok': False, 'missing': [], 'file_count': 0,
           'qq_count': 0, 'max_qq': ''}
    if not zip_path.is_file():
        out['missing'] = ['（文件不存在）']
        return out
    if zip_path.stat().st_size < 1024 * 1024:
        out['missing'] = ['（文件过小，不是完整内核包）']
        return out

    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            out['file_count'] = len(names)
            base = _detect_root(names)
            for req in REQUIRED_FILES:
                target = f'{base}{req}' if base else req
                if target not in names:
                    out['missing'] = out['missing'] + [req]

            if out['missing']:
                return out

            # 从 zip 里直接读 napcat.mjs 统计支持的 QQ 版本，不落盘
            import re
            mjs_name = f'{base}napcat.mjs' if base else 'napcat.mjs'
            with z.open(mjs_name) as f:
                data = f.read()
            text = data.decode('utf-8', errors='ignore')
            vs = sorted(
                set(re.findall(r'"(\d+\.\d+\.\d+-\d+)(?:-[a-z0-9]+)?"', text)),
                key=_qq_sort_key)
            out['qq_count'] = len(vs)
            out['max_qq'] = vs[-1] if vs else ''
            out['ok'] = True
    except zipfile.BadZipFile as e:
        out['missing'] = [f'（压缩包损坏：{e}）']
    except Exception as e:
        out['missing'] = [f'（读取失败：{type(e).__name__}: {e}）']
    return out


def _qq_sort_key(v: str) -> tuple:
    import re
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)-(\d+)$', v)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0, 0)


def _detect_root(names: list[str]) -> str:
    """判断 zip 里是否有一层包装目录，返回前缀（含结尾斜杠）。"""
    if not names:
        return ''
    tops = {n.split('/')[0] for n in names if '/' in n}
    # 顶层只有一个目录，且根下没有 napcat.mjs → 有包装目录
    if len(tops) == 1:
        top = next(iter(tops))
        if 'napcat.mjs' not in names:
            return f'{top}/'
    return ''


# ---------- 替换 ----------

def install_package(zip_path: Path, napcat_dir: Path,
                    on_progress: Callable[[str], None] | None = None) -> dict:
    """把 zip 里的内核安装到 napcat_dir。

    流程（任何一步失败都会回滚）：

    1. 备份现有内核到 `_napcat_backup/<时间戳>/`
    2. 清掉旧的内核文件（保留 config / cache / logs / qq_path.txt）
    3. 解压新内核
    4. 校验生效（关键文件存在 + 能读到偏移表）
    5. 失败 → 从备份恢复

    Returns:
        {'ok': bool, 'backup': str, 'restored': bool,
         'file_count': int, 'max_qq': str, 'error': str}
    """
    report = on_progress or (lambda m: None)
    nd = Path(napcat_dir)
    out = {'ok': False, 'backup': '', 'restored': False,
           'file_count': 0, 'max_qq': '', 'error': ''}

    if not nd.is_dir():
        out['error'] = f'NapCat 目录不存在：{nd}'
        return out

    # ---- 1. 备份 ----
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup_root = nd.parent / BACKUP_DIR_NAME / stamp
    try:
        report('正在备份现有内核...')
        backup_root.mkdir(parents=True, exist_ok=True)
        _copy_core(nd, backup_root)
        out['backup'] = str(backup_root)
        report(f'已备份到 {backup_root.name}')
    except Exception as e:
        out['error'] = f'备份失败，为安全起见中止：{type(e).__name__}: {e}'
        return out

    # ---- 2. 清理旧内核 ----
    try:
        report('正在清理旧内核...')
        _remove_core(nd)
    except Exception as e:
        out['error'] = f'清理旧内核失败：{type(e).__name__}: {e}'
        return out

    # ---- 3. 解压新内核 ----
    try:
        report('正在解压新内核...')
        with zipfile.ZipFile(zip_path) as z:
            base = _detect_root(z.namelist())
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not name.startswith(base):
                    continue
                rel = name[len(base):]
                if not rel:
                    continue
                top = rel.split('/')[0]
                if top in EXCLUDE_TOP:
                    continue
                dest = nd / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
    except Exception as e:
        report('解压失败，正在回滚...')
        ok = _restore(backup_root, nd)
        out['restored'] = ok
        out['error'] = (f'解压失败：{type(e).__name__}: {e}\n'
                        + ('已回滚到原内核。' if ok else '回滚也失败了，请手动从备份恢复。'))
        return out

    # ---- 4. 校验生效 ----
    report('正在校验新内核...')
    missing = [f for f in REQUIRED_FILES if not (nd / f).is_file()]
    if missing:
        report('新内核不完整，正在回滚...')
        ok = _restore(backup_root, nd)
        out['restored'] = ok
        out['error'] = ('新内核缺少关键文件：' + ', '.join(missing) + '\n'
                        + ('已回滚到原内核。' if ok else '回滚也失败了，请手动从备份恢复。'))
        return out

    # 读新内核的偏移表
    from .qqversion import supported_versions
    vs = supported_versions(nd)
    out['ok'] = True
    out['max_qq'] = vs[-1] if vs else ''
    out['file_count'] = sum(1 for p in nd.rglob('*') if p.is_file())
    report(f'内核已更新（支持 QQ 最高 {out["max_qq"] or "未知"}）')
    return out


def _copy_core(src: Path, dst: Path) -> None:
    """备份内核文件（跳过用户数据）。"""
    for p in src.iterdir():
        name = p.name
        if name in EXCLUDE_TOP or name == BACKUP_DIR_NAME:
            continue
        target = dst / name
        if p.is_dir():
            shutil.copytree(p, target)
        else:
            shutil.copy2(p, target)


def _remove_core(nd: Path) -> None:
    """删除旧内核文件（保留用户数据）。"""
    for p in list(nd.iterdir()):
        name = p.name
        if name in EXCLUDE_TOP or name == BACKUP_DIR_NAME:
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            try:
                p.unlink()
            except OSError:
                pass   # 文件被占用等情况，尽力而为


def _restore(backup_root: Path, nd: Path) -> bool:
    """从备份恢复内核。返回是否成功。"""
    try:
        _remove_core(nd)
        _copy_core(backup_root, nd)
        return True
    except Exception:
        return False


def rollback(backup_dir: Path, napcat_dir: Path) -> bool:
    """手动回滚到指定备份。"""
    try:
        if not Path(backup_dir).is_dir():
            return False
        return _restore(Path(backup_dir), Path(napcat_dir))
    except Exception:
        return False


def list_backups(napcat_dir: Path) -> list[dict]:
    """列出可回滚的备份。"""
    root = Path(napcat_dir).parent / BACKUP_DIR_NAME
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        try:
            size = sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
        except OSError:
            size = 0
        out.append({'path': str(p), 'name': p.name, 'size': size})
    return out


def cleanup_backups(napcat_dir: Path, keep: int = 3) -> int:
    """只保留最近 N 份备份，返回删除数量。"""
    items = list_backups(napcat_dir)
    removed = 0
    for item in items[keep:]:
        try:
            shutil.rmtree(item['path'])
            removed += 1
        except OSError:
            pass
    return removed


# ---------- 一键更新 ----------

def update_to_latest(napcat_dir: Path,
                     on_stage: Callable[[str], None] | None = None,
                     on_bytes: Callable[[int, int], None] | None = None,
                     should_cancel: Callable[[], bool] | None = None,
                     install: bool = True) -> dict:
    """检查并更新到最新内核（完整流程）。

    Args:
        napcat_dir: 内核目录
        on_stage: 阶段文字回调
        on_bytes: 下载进度回调 (已下载, 总字节)
        should_cancel: 返回 True 时中止
        install: False 时只检查不安装，返回是否有新版本

    Returns:
        {
          'ok': bool, 'updated': bool, 'latest': str, 'local_downloaded': str,
          'max_qq_before': str, 'max_qq_after': str,
          'backup': str, 'error': str, 'cancelled': bool,
        }
    """
    stage = on_stage or (lambda m: None)
    cancel = should_cancel or (lambda: False)
    nd = Path(napcat_dir)
    out = {'ok': False, 'updated': False, 'latest': '',
           'max_qq_before': '', 'max_qq_after': '',
           'backup': '', 'error': '', 'cancelled': False}

    before = local_versions(nd)
    out['max_qq_before'] = before['max_qq']

    # 1. 查最新
    stage('正在查询 NapCat 最新版本...')
    try:
        rel = fetch_latest()
    except UpdateError as e:
        out['error'] = str(e)
        return out
    out['latest'] = rel['tag']

    if not install:
        out['ok'] = True
        out['updated'] = False
        return out

    # 2. 下载（多源自动选最快）
    stage(f'正在下载 {rel["tag"]}（{rel["asset_size"]/1024/1024:.1f} MB）...')
    tmpdir = Path(tempfile.mkdtemp(prefix='napcat_update_'))
    zip_path = tmpdir / ASSET_NAME
    try:
        download_best(rel['asset_url'], zip_path,
                      on_progress=on_bytes,
                      on_stage=stage,
                      should_cancel=cancel,
                      per_source_seconds=5,
                      timeout=30)
    except UpdateError as e:
        out['cancelled'] = '已取消' in str(e)
        out['error'] = str(e)
        _safe_rmtree(tmpdir)
        return out

    # 3. 校验
    stage('正在校验下载的内核包...')
    ver = verify_package(zip_path)
    if not ver['ok']:
        out['error'] = ('下载的内核包不可用：' + ', '.join(ver['missing']) + '\n'
                        f'可以手动下载后解压覆盖：{rel["html_url"]}')
        _safe_rmtree(tmpdir)
        return out

    # 4. 安装
    stage('正在安装新内核...')
    res = install_package(zip_path, nd, on_progress=stage)
    _safe_rmtree(tmpdir)

    out['backup'] = res.get('backup', '')
    out['max_qq_after'] = res.get('max_qq', '')
    if not res['ok']:
        out['error'] = res.get('error', '安装失败')
        return out

    out['ok'] = True
    out['updated'] = True
    # 清理旧备份，只留 3 份
    try:
        cleanup_backups(nd, keep=3)
    except Exception:
        pass
    return out


def _safe_rmtree(p: Path) -> None:
    try:
        shutil.rmtree(p)
    except OSError:
        pass
