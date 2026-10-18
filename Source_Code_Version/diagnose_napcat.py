"""NapCat 环境诊断工具

用法：
    python diagnose_napcat.py

用于排查「扫码登录卡住 / 获取不到二维码 / 服务红点」这类问题。
会检查：端口、WebUI 鉴权、各候选目录的 token 与二维码文件状态。
"""

import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def find_candidates() -> list[Path]:
    """收集可能的 NapCat 目录。"""
    out: list[Path] = []
    seen = set()

    def add(p: Path):
        try:
            rp = p.resolve()
        except Exception:
            return
        if rp in seen or not rp.is_dir():
            return
        # 必须像 NapCat
        if not (rp / 'napcat.mjs').exists():
            return
        seen.add(rp)
        out.append(rp)

    # 1. 本文件所在目录及上级
    here = Path(__file__).resolve().parent
    for base in (here, here.parent, here.parent.parent):
        for name in ('napcat_green', 'napcat', 'NapCat', 'NapCatQQ'):
            add(base / name)
        # 直接就是 NapCat 目录
        add(base)

    # 2. 常见安装位置
    for root in (Path('C:/NapCat'), Path('D:/NapCat'), Path('C:/NapCatQQ')):
        if not root.is_dir():
            continue
        try:
            for sub in sorted(root.iterdir(), reverse=True):
                if sub.is_dir():
                    add(sub)
        except OSError:
            pass
        add(root)

    return out


def try_auth(port: int, token: str) -> tuple[bool, str]:
    """用 token 尝试登录 WebUI。"""
    h = hashlib.sha256(f'{token}.napcat'.encode()).hexdigest()
    body = json.dumps({'hash': h}).encode()
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/auth/login',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            resp = json.loads(r.read().decode(errors='replace'))
        if resp.get('code') == 0:
            cred = (resp.get('data') or {}).get('Credential') or ''
            return True, cred[:16]
        return False, str(resp.get('message') or resp.get('code'))
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code}'
    except Exception as e:
        return False, f'{type(e).__name__}'


def check_status(port: int, cred: str) -> dict | None:
    """用凭证查登录状态。"""
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/QQLogin/CheckLoginStatus',
        data=b'{}',
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {cred}'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = json.loads(r.read().decode(errors='replace'))
        if resp.get('code') == 0:
            return resp.get('data') or {}
        return {'_error': str(resp.get('message') or resp.get('code'))}
    except Exception as e:
        return {'_error': f'{type(e).__name__}: {e}'}


def main() -> int:
    print('=' * 70)
    print('  NapCat 环境诊断')
    print('=' * 70)

    print('\n【1】端口检查')
    webui_on = port_open('127.0.0.1', 6099)
    onebot_on = port_open('127.0.0.1', 3000)
    print(f'  6099 (WebUI) : {"监听中" if webui_on else "未监听"}')
    print(f'  3000 (OneBot): {"监听中" if onebot_on else "未监听"}')

    if not webui_on:
        print('\n  ✗ WebUI 未监听 → NapCat 没有运行')
        print('    解决：启动 NapCat（程序里的「启动服务」按钮，')
        print('          或手动运行 napcat_green\\启动NapCat.bat）')
        return 1

    print('\n【2】查找 NapCat 目录')
    cands = find_candidates()
    if not cands:
        print('  未找到任何含 napcat.mjs 的目录')
    else:
        for d in cands:
            print(f'  - {d}')

    print('\n【3】逐个测试 webui.json 的 token')
    working: list[tuple[Path, str, int]] = []
    for d in cands:
        wj = d / 'config' / 'webui.json'
        if not wj.exists():
            print(f'\n  {d}')
            print(f'    ✗ 没有 config/webui.json')
            continue

        try:
            cfg = json.loads(wj.read_text(encoding='utf-8-sig'))
        except Exception as e:
            print(f'\n  {d}')
            print(f'    ✗ webui.json 解析失败: {e}')
            continue

        token = str(cfg.get('token') or '')
        port = int(cfg.get('port') or 6099)
        print(f'\n  {d}')
        print(f'    token={token[:10]}{"..." if len(token) > 10 else ""}  port={port}')

        if not token:
            print(f'    ✗ token 为空')
            continue

        ok, info = try_auth(port, token)
        if ok:
            print(f'    ✓ 鉴权成功  credential={info}...')
            working.append((d, info, port))
            st = check_status(port, info)
            if st and '_error' not in st:
                phase = st.get('loginPhase', '?')
                is_login = st.get('isLogin')
                print(f'      登录状态: isLogin={is_login}  phase={phase}')
                err = st.get('loginError')
                if err:
                    print(f'      错误: {err}')
            elif st:
                print(f'      状态查询失败: {st["_error"]}')
        else:
            print(f'    ✗ 鉴权失败: {info}')

    print('\n【4】二维码文件状态')
    for d in cands:
        qr = d / 'cache' / 'qrcode.png'
        if qr.exists():
            age = time.time() - qr.stat().st_mtime
            fresh = age <= 300
            print(f'  {d.name}: {age/60:.1f} 分钟前  {"新鲜 ✓" if fresh else "已过期 ✗"}')
        else:
            print(f'  {d.name}: 不存在')

    print('\n' + '=' * 70)
    print('  诊断结论')
    print('=' * 70)

    if working:
        d, cred, port = working[0]
        print(f'\n  ✓ 可用目录: {d}')
        print(f'\n  请在程序的「设置」页把 NapCat 目录设为上述路径。')
        print(f'  或者在 config.json 里设置：')
        print(f'    "napcat_dir": "{str(d).replace(chr(92), chr(92)*2)}"')
    else:
        print('\n  ✗ 所有候选目录的 token 都无法通过鉴权')
        print('\n  这通常意味着：')
        print('    1. 正在运行的 NapCat 不是从这些目录启动的；或')
        print('    2. NapCat 的 webui.json 在启动后被改过（token 只在启动时读取）；或')
        print('    3. 运行中的 NapCat 与磁盘上的配置不是同一份')
        print('\n  解决办法（按推荐顺序）：')
        print('    A. 关掉当前 NapCat，重新用程序启动（会读取正确的 webui.json）')
        print('       在程序里点「启动服务」，或手动运行 napcat_green\\启动NapCat.bat')
        print('    B. 如果 NapCat 是从别处启动的，找到那个目录，把它的路径填进程序设置')
        print('    C. 极端情况：删除 webui.json 后重启 NapCat，会重新生成 token')
        print('       注意：删除前先备份')
        if not onebot_on:
            print('\n  另外：OneBot (3000) 未监听，说明 QQ 还没登录成功。')
            print('        先解决登录问题，再回来验证服务状态。')

    return 0 if working else 2


if __name__ == '__main__':
    raise SystemExit(main())
