"""NapCat 客户端

封装与 NapCat（OneBot 11 协议端）的所有交互：
- 拉取群消息（分页回溯）
- 获取群列表 / 账号列表
- 账号切换（改配置 + 重启 NapCat）
- 检查服务状态

所有网络调用都是同步的，GUI 里需放进工作线程。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import Config


class NapCatError(Exception):
    """NapCat 交互失败。"""


class OneBotClient:
    """OneBot 11 HTTP 客户端（无状态，配置从 Config 动态读取）。"""

    def __init__(self, config: Config, log: Callable[[str], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None):
        self.config = config
        self._log = log or (lambda m: None)
        self._should_cancel = should_cancel or (lambda: False)

    # ---------- 底层调用 ----------

    def call(self, action: str, payload: dict | None = None,
             timeout: int = 60) -> Any:
        """调用一个 OneBot action，返回 data 字段。"""
        url = f'{self.config.napcat_base}/{action}'
        body = json.dumps(payload or {}).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode(errors='replace')
        except urllib.error.HTTPError as e:
            raise NapCatError(
                f'{action} HTTP {e.code}: {e.read().decode(errors="replace")[:200]}'
            ) from e
        except urllib.error.URLError as e:
            raise NapCatError(
                f'无法连接 NapCat（{self.config.napcat_base}）：{e.reason}\n'
                f'请确认 NapCat 服务已启动（双击「启动服务.bat」）'
            ) from e
        except Exception as e:
            raise NapCatError(f'{action} 调用失败: {type(e).__name__}: {e}') from e

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise NapCatError(f'{action} 返回非法 JSON: {raw[:200]}') from e

        if data.get('status') == 'failed':
            raise NapCatError(
                f'{action} 失败: {data.get("message") or data.get("wording") or raw[:200]}'
            )
        return data.get('data')

    # ---------- 状态检查 ----------

    def is_online(self, timeout: int = 5) -> bool:
        try:
            self.call('get_login_info', timeout=timeout)
            return True
        except Exception:
            return False

    def login_info(self) -> dict:
        return self.call('get_login_info', timeout=10) or {}

    def version_info(self) -> dict:
        return self.call('get_version_info', timeout=10) or {}

    # ---------- 群列表 ----------

    def group_list(self, no_cache: bool = False) -> list[dict]:
        """获取当前账号加入的群列表。"""
        data = self.call('get_group_list', {'no_cache': no_cache}, timeout=60)
        if not isinstance(data, list):
            return []
        out = []
        for g in data:
            if not isinstance(g, dict):
                continue
            out.append({
                'group_id': str(g.get('group_id', '')),
                'group_name': str(g.get('group_name', '')),
                'member_count': g.get('member_count', 0),
                'max_member_count': g.get('max_member_count', 0),
                'group_remark': str(g.get('group_remark', '')),
            })
        out.sort(key=lambda x: x['group_name'])
        return out

    def group_info(self, group_id: str) -> dict:
        return self.call('get_group_info', {'group_id': str(group_id)}, timeout=30) or {}

    # ---------- 消息拉取 ----------

    def _history_page(self, group_id: str, count: int,
                      anchor: str | None = None,
                      timeout: int = 90) -> list[dict]:
        """拉一页历史消息。

        关键：带 message_seq（最旧消息的 message_id）+ reverse_order=true，
        否则每页都返回同一段最新消息（NapCat 的分页要求）。
        """
        payload: dict[str, Any] = {
            'group_id': str(group_id),
            'count': int(count),
        }
        if anchor is not None:
            payload['message_seq'] = anchor
            payload['reverse_order'] = True
        data = self.call('get_group_msg_history', payload, timeout=timeout)
        if isinstance(data, dict):
            msgs = data.get('messages') or []
            return [m for m in msgs if isinstance(m, dict)]
        return []

    def fetch_messages(self, group_id: str, start_ts: float, end_ts: float,
                       max_count: int = 60000, page_size: int = 200,
                       max_pages: int = 400, max_retry: int = 3,
                       on_progress: Callable[[str, int], None] | None = None,
                       ) -> list[dict]:
        """分页回溯，收集 [start_ts, end_ts) 区间内的消息。

        单页失败不丢弃已拉数据；连续失败 5 页才放弃。
        """
        collected: list[dict] = []
        seen: set[str] = set()
        anchor: str | None = None
        failed_pages = 0
        report = on_progress or (lambda msg, n: None)

        for page in range(1, max_pages + 1):
            if self._should_cancel():
                report('已取消', len(collected))
                break

            data = None
            last_err: Exception | None = None
            for attempt in range(1, max_retry + 1):
                try:
                    data = self._history_page(group_id, page_size, anchor)
                    break
                except Exception as e:
                    last_err = e
                    if attempt < max_retry:
                        time.sleep(2 * attempt)

            if data is None:
                failed_pages += 1
                self._log(f'  第 {page} 页失败（重试 {max_retry} 次）: '
                          f'{type(last_err).__name__}: {str(last_err)[:80]}')
                if failed_pages >= 5:
                    self._log(f'  连续 {failed_pages} 页失败，停止回溯'
                              f'（已收集 {len(collected)} 条）')
                    break
                self._log(f'  跳过该页，继续回溯（已收集 {len(collected)} 条）')
                continue

            failed_pages = 0
            if not data:
                break

            for m in data:
                mid = str(m.get('message_id'))
                if mid in seen:
                    continue
                seen.add(mid)
                ts = m.get('time')
                if ts and start_ts <= ts < end_ts:
                    collected.append(m)

            times = [m['time'] for m in data if m.get('time')]
            if not times:
                break

            if page % 10 == 0:
                lo = dt.datetime.fromtimestamp(min(times))
                report(f'已翻 {page} 页，收集 {len(collected)} 条，'
                       f'回溯到 {lo:%m-%d %H:%M}', len(collected))

            # 翻过区间起点就停
            if min(times) < start_ts:
                break
            if len(collected) >= max_count:
                break

            oldest = min(data, key=lambda x: x.get('time', 0))
            new_anchor = oldest.get('message_id')
            if new_anchor is None or new_anchor == anchor:
                break
            anchor = new_anchor

        collected.sort(key=lambda m: m.get('time', 0))
        return collected

    def check_completeness(self, msgs: list[dict], start_ts: float,
                           end_ts: float, gap_seconds: float = 900) -> dict:
        """检查拉取的数据是否覆盖完整区间。

        返回 {'complete': bool, 'head_gap': 秒, 'tail_gap': 秒,
              'got_lo': ts, 'got_hi': ts}
        """
        if not msgs:
            return {'complete': False, 'head_gap': end_ts - start_ts,
                    'tail_gap': 0.0, 'got_lo': start_ts, 'got_hi': start_ts}
        got_lo = msgs[0].get('time', start_ts)
        got_hi = msgs[-1].get('time', end_ts)
        head_gap = max(0.0, got_lo - start_ts)
        tail_gap = max(0.0, end_ts - got_hi - 60)
        return {
            'complete': head_gap <= gap_seconds and tail_gap <= gap_seconds,
            'head_gap': head_gap,
            'tail_gap': tail_gap,
            'got_lo': got_lo,
            'got_hi': got_hi,
        }


# ---------- 服务管理 ----------

class NapCatService:
    """NapCat 服务进程管理（启动/停止/账号切换）。"""

    def __init__(self, config: Config, log: Callable[[str], None] | None = None):
        self.config = config
        self._log = log or (lambda m: None)

    @property
    def napcat_dir(self) -> Path:
        """NapCat 目录。

        配置为空时 `Path('')` 会退化成当前工作目录（`.`），导致程序去
        `./cache/qrcode.png` 找二维码，永远找不到。这里显式兜底到配置探测
        出的目录；再不行就返回当前目录，让调用方通过 is_dir() 判断。
        """
        raw = str(self.config.get('napcat_dir') or '').strip()
        if raw:
            return Path(raw)
        # 配置为空：用 config 层的自动探测结果（Config.load 已经写过一次，
        # 但如果运行期被清空，这里再兜一次）
        try:
            from ..config import detect_napcat_dir
            found = detect_napcat_dir()
            if found:
                return Path(found)
        except Exception:
            pass
        return Path.cwd()

    @property
    def config_dir(self) -> Path:
        return self.napcat_dir / 'config'

    @property
    def start_bat(self) -> Path:
        """启动脚本路径（按优先级探测，兼容官方版与绿色版）。

        官方包叫 start.bat，绿色分发包叫 启动NapCat.bat。
        """
        for name in ('start.bat', '启动NapCat.bat',
                     'launcher-win10.bat', 'launcher.bat'):
            p = self.napcat_dir / name
            if p.exists():
                return p
        # 都不存在：返回默认名，由调用方报错（便于提示用户）
        return self.napcat_dir / 'start.bat'

    @property
    def qrcode_file(self) -> Path:
        """二维码图片路径（NapCat 内核在等扫码时写入）。"""
        return self.napcat_dir / 'cache' / 'qrcode.png'

    @property
    def hidden_launcher(self) -> Path:
        """隐藏提权启动器（VBS）。绿色包自带；官方包可能没有。"""
        return self.napcat_dir / 'launch_hidden.vbs'

    # ---- 进程 ----

    def running_pids(self) -> dict[str, list[int]]:
        """返回 {'QQ': [pid...], 'NapCatWinBootMain': [pid...]}"""
        out: dict[str, list[int]] = {'QQ': [], 'NapCatWinBootMain': []}
        try:
            r = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq QQ.exe', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=15,
                encoding='gbk', errors='replace')
            for line in r.stdout.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == 'qq.exe':
                    try:
                        out['QQ'].append(int(parts[1]))
                    except ValueError:
                        pass
        except Exception:
            pass
        try:
            r = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq NapCatWinBootMain.exe',
                 '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=15,
                encoding='gbk', errors='replace')
            for line in r.stdout.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and 'napcat' in parts[0].lower():
                    try:
                        out['NapCatWinBootMain'].append(int(parts[1]))
                    except ValueError:
                        pass
        except Exception:
            pass
        return out

    def kill_all(self) -> None:
        """结束所有 QQ 相关进程。"""
        for name in ('NapCatWinBootMain.exe', 'QQ.exe', 'QQEX.exe'):
            try:
                subprocess.run(['taskkill', '/F', '/IM', name],
                               capture_output=True, timeout=20)
            except Exception:
                pass
        time.sleep(3)

    def start(self, auto: bool = True) -> None:
        """启动 NapCat。

        auto=True 时走无人值守分支：不弹交互提问（set /p 在隐藏窗口下会永久
        卡死）、不等 pause、QQ 占用时自动关闭。GUI 调用一律用 auto=True。

        启动方式优先用隐藏提权启动器（launch_hidden.vbs）—— 注入 QQ 需要管理员
        权限，而提权那一层用 `cmd /c start` 无法隐藏，会弹出可见控制台。VBS 用
        ShellExecute(runas, 窗口模式=0) 一次拿到提权与隐藏。绿色包自带该脚本；
        找不到时回退到 cmd 方式（仍能工作，但提权时会看到窗口）。
        """
        bat = self.start_bat
        if not bat.exists():
            raise NapCatError(f'找不到启动脚本: {bat}')

        vbs = self.hidden_launcher
        if vbs.exists():
            args = ['wscript.exe', '//nologo', str(vbs), str(bat)]
            if auto:
                args.append('--auto')
            subprocess.Popen(
                args,
                cwd=str(self.napcat_dir),
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            return

        # 回退：无 VBS 时用 cmd start
        args = ['cmd', '/c', 'start', '', '/min', str(bat)]
        if auto:
            args.append('--auto')
        subprocess.Popen(
            args,
            cwd=str(self.napcat_dir),
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )

    def wait_ready(self, timeout: int = 180, interval: int = 5,
                   on_progress: Callable[[int, int], None] | None = None) -> bool:
        """轮询等待 NapCat 就绪（3000 端口可用）。

        on_progress(已等秒数, 总秒数)
        """
        client = OneBotClient(self.config)
        waited = 0
        while waited < timeout:
            if self._should_cancel_stub():
                return False
            if client.is_online(timeout=3):
                return True
            time.sleep(interval)
            waited += interval
            if on_progress:
                on_progress(waited, timeout)
        return False

    def _should_cancel_stub(self) -> bool:
        return False

    # ---- 账号 ----

    def read_webui_token(self) -> str | None:
        """从 webui.json 读 WebUI token。"""
        p = self.config_dir / 'webui.json'
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding='utf-8-sig'))
            return str(d.get('token') or '').strip() or None
        except Exception:
            return None

    def _webui_login(self, token: str) -> str | None:
        """用 token 换 WebUI 凭证（有效期 1 小时）。"""
        h = hashlib.sha256(f'{token}.napcat'.encode()).hexdigest()
        body = json.dumps({'hash': h}).encode()
        req = urllib.request.Request(
            f'{self.config.get("napcat_webui", "http://127.0.0.1:6099").rstrip("/")}/api/auth/login',
            data=body, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            data = d.get('data') or {}
            return data.get('Credential') or data.get('credential')
        except Exception:
            return None

    def quick_login_list(self) -> list[dict]:
        """获取本机可快速登录的账号列表（带昵称）。

        优先走 WebUI API；失败则降级到文件系统枚举。
        """
        token = self.read_webui_token()
        if token:
            cred = self._webui_login(token)
            if cred:
                base = str(self.config.get('napcat_webui', 'http://127.0.0.1:6099')).rstrip('/')
                for path in ('/api/QQLogin/GetQuickLoginListNew',
                             '/api/QQLogin/GetQuickLoginList'):
                    try:
                        req = urllib.request.Request(
                            f'{base}{path}',
                            headers={'Authorization': f'Bearer {cred}'})
                        with urllib.request.urlopen(req, timeout=15) as r:
                            d = json.loads(r.read().decode())
                        items = d.get('data') or []
                        out = []
                        for it in items:
                            if isinstance(it, str):
                                out.append({'uin': it, 'nickname': '', 'face': ''})
                            elif isinstance(it, dict):
                                out.append({
                                    'uin': str(it.get('uin') or ''),
                                    'nickname': str(it.get('nickName') or it.get('nickname') or ''),
                                    'face': str(it.get('faceUrl') or ''),
                                })
                        if out:
                            return out
                    except Exception:
                        continue
        return self._accounts_from_fs()

    def _accounts_from_fs(self) -> list[dict]:
        """降级方案：从文件系统枚举登录过的账号。"""
        out: list[dict] = []
        seen: set[str] = set()
        # 优先读 MSFAuthUinsV1.dat（明文逗号分隔）
        dat = Path.home() / 'Documents' / 'Tencent Files' / 'nt_qq' / 'global' \
            / 'nt_data' / '.MSFSDKDataDir' / '.MSFAuthUin' / '.MSFAuthUinsV1.dat'
        if dat.exists():
            try:
                txt = dat.read_text(encoding='utf-8', errors='ignore')
                for uin in re.findall(r'\d{5,12}', txt):
                    if uin not in seen:
                        seen.add(uin)
                        out.append({'uin': uin, 'nickname': '', 'face': ''})
            except Exception:
                pass
        if out:
            return out
        # 再看 Tencent Files 目录
        tf = Path.home() / 'Documents' / 'Tencent Files'
        if tf.exists():
            for d in tf.iterdir():
                if d.is_dir() and d.name.isdigit():
                    out.append({'uin': d.name, 'nickname': '', 'face': ''})
        return out

    def set_quick_account(self, uin: str) -> bool:
        """把指定账号写入 .env（下次启动自动登录它）。"""
        env = self.config_dir / '.env'
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            env.write_text(
                f'# 自动快速登录的 QQ 号\nNAPCAT_QUICK_ACCOUNT={uin}\n',
                encoding='utf-8')
            return True
        except Exception as e:
            self._log(f'写入 .env 失败: {e}')
            return False

    def switch_account(self, uin: str,
                       on_step: Callable[[str], None] | None = None,
                       on_wait: Callable[[int, int], None] | None = None) -> bool:
        """切换 QQ 账号（杀进程 → 改配置 → 重启 → 等待就绪）。

        注意：整个过程约 1-3 分钟，期间服务不可用。
        on_step(说明文字)  每个阶段回调
        on_wait(已秒, 总秒) 等待期间回调
        """
        step = on_step or (lambda s: None)

        step('正在结束当前 QQ 进程...')
        self.kill_all()

        step(f'正在设置自动登录账号为 {uin}...')
        if not self.set_quick_account(uin):
            return False

        step('正在启动 NapCat...')
        self.start()

        step('等待服务就绪（约 1-3 分钟）...')
        ok = self.wait_ready(timeout=180, on_progress=on_wait)
        if not ok:
            step('等待超时，请检查 QQ 是否弹出了登录窗口')
            return False

        step('账号切换完成')
        return True

    # ---- 自动启动与登录引导 ----

    def webui(self, log: Callable[[str], None] | None = None) -> 'NapCatWebUI':
        """获取 WebUI 客户端（用于查询登录状态与二维码）。"""
        return NapCatWebUI(self.config, log or self._log)

    def ensure_running(self, on_phase: Callable[[str, dict], None] | None = None,
                       should_cancel: Callable[[], bool] | None = None,
                       timeout: int = 240,
                       poll_interval: float = 2.0) -> dict:
        """确保 NapCat 已启动并完成登录。

        这是「自动检测 → 自动启动 → 扫码引导」的核心流程。
        全程只用 WebUI API（6099）—— 因为 OneBot（3000）要登录后才可用。

        Args:
            on_phase: 状态回调 (phase, data)，phase 取值见 PHASE_* 常量
            should_cancel: 返回 True 时立即中止
            timeout: 总超时秒数
            poll_interval: 轮询间隔

        Returns:
            {'ok': bool, 'phase': str, 'info': dict, 'error': str}
        """
        phase_cb = on_phase or (lambda p, d: None)
        cancel = should_cancel or (lambda: False)
        webui = self.webui()

        # ---- 1. 检查 WebUI 是否可达（登录前后都可访问）----
        phase_cb(PHASE_CHECKING, {})
        if not webui.is_available(timeout=3):
            # ---- 2. 未运行 → 启动 ----
            bat = self.start_bat
            if not bat.exists():
                msg = (f'找不到 NapCat 启动脚本。\n'
                       f'期望位置：{self.napcat_dir}\n'
                       f'请确认 NapCat 目录配置正确（设置页可修改）。')
                phase_cb(PHASE_ERROR, {'error': msg})
                return {'ok': False, 'phase': PHASE_ERROR, 'info': {}, 'error': msg}

            phase_cb(PHASE_STARTING, {'script': bat.name})
            try:
                self.start()
            except Exception as e:
                msg = f'启动 NapCat 失败：{type(e).__name__}: {e}'
                phase_cb(PHASE_ERROR, {'error': msg})
                return {'ok': False, 'phase': PHASE_ERROR, 'info': {}, 'error': msg}

            # ---- 3. 等待 WebUI 出现 ----
            waited = 0.0
            while waited < 60:
                if cancel():
                    phase_cb(PHASE_CANCELLED, {})
                    return {'ok': False, 'phase': PHASE_CANCELLED, 'info': {},
                            'error': '已取消'}
                time.sleep(poll_interval)
                waited += poll_interval
                phase_cb(PHASE_STARTING, {'waited': int(waited)})
                if webui.is_available(timeout=2):
                    break
            else:
                msg = ('等待 NapCat 启动超时。\n'
                       '可能原因：\n'
                       '  1. 弹出的 UAC 提权窗口未确认（需要点「是」）\n'
                       '  2. QQ 客户端未安装或路径异常\n'
                       '  3. NapCat 版本与 QQ 版本不匹配')
                phase_cb(PHASE_ERROR, {'error': msg})
                return {'ok': False, 'phase': PHASE_ERROR, 'info': {}, 'error': msg}

        # ---- 4. 轮询登录状态直到就绪 ----
        deadline = time.time() + timeout
        last_phase = ''
        while time.time() < deadline:
            if cancel():
                phase_cb(PHASE_CANCELLED, {})
                return {'ok': False, 'phase': PHASE_CANCELLED, 'info': {},
                        'error': '已取消'}

            status = webui.login_status()
            phase = str(status.get('loginPhase') or '').strip()
            is_login = bool(status.get('isLogin'))

            if is_login and phase in ('ready', 'initializing', ''):
                # 登录成功（initializing 也算成功，OneBot 可能还在起）
                for _ in range(20):   # 额外等 OneBot 端口就绪
                    if cancel():
                        break
                    if OneBotClient(self.config).is_online(timeout=2):
                        break
                    time.sleep(1.5)
                info = webui.login_info() or {}
                if not info:
                    info = OneBotClient(self.config).login_info()
                phase_cb(PHASE_READY, {'info': info})
                return {'ok': True, 'phase': PHASE_READY, 'info': info, 'error': ''}

            # 状态变化时上报（避免刷屏）
            if phase != last_phase:
                last_phase = phase
                phase_cb(phase or PHASE_UNKNOWN, {
                    'qrcode_url': status.get('qrcodeurl', ''),
                    'is_login': is_login,
                    'login_error': status.get('loginError', ''),
                })
            else:
                phase_cb(phase or PHASE_UNKNOWN, status)

            time.sleep(poll_interval)

        msg = ('等待 QQ 登录超时。\n'
               '如果二维码已过期，可点「刷新二维码」重试；\n'
               '或关闭 QQ 后重试。')
        phase_cb(PHASE_ERROR, {'error': msg})
        return {'ok': False, 'phase': PHASE_ERROR, 'info': {}, 'error': msg}

    def qrcode_bytes(self, max_age: float = 0) -> bytes | None:
        """读取当前二维码图片（字节）。

        Args:
            max_age: 只接受不超过此秒数的图（0 表示不限，用于避免展示过期的旧图）

        注意：这个接口把「文件不存在 / 已过期 / 读取失败」都压成 None，
        调用方无法区分原因。需要具体原因请用 qrcode_state()。
        """
        st = self.qrcode_state(max_age=max_age)
        return st.get('data')

    def qrcode_state(self, max_age: float = 300) -> dict:
        """检查二维码文件的详细状态（供 UI 展示具体原因）。

        与 qrcode_bytes() 的区别：这个接口不吞掉失败原因，让界面能告诉用户
        「到底是文件还没生成，还是已经过期了，还是读取失败」。

        Args:
            max_age: 超过这个秒数的图视为过期（默认 300 秒 = 5 分钟）

        Returns:
            {
              'ok': bool,          # True 表示 data 可用
              'data': bytes|None,  # 图片字节
              'reason': str,       # ok | no_dir | no_file | stale | read_error
              'age': float,        # 文件年龄（秒），未知时为 -1
              'path': str,         # 检查的文件路径（便于排查）
              'message': str,      # 给用户看的一句话说明
            }
        """
        out = {
            'ok': False, 'data': None, 'reason': 'no_file',
            'age': -1.0, 'path': '', 'message': '',
        }

        try:
            p = self.qrcode_file
        except Exception as e:
            out['reason'] = 'no_dir'
            out['message'] = f'无法解析 NapCat 目录：{type(e).__name__}'
            return out

        out['path'] = str(p)

        # ---- 目录不存在 ----
        base = self.napcat_dir
        if not base.is_dir():
            out['reason'] = 'no_dir'
            out['message'] = f'NapCat 目录不存在：{base}'
            return out

        # ---- 文件不存在 ----
        try:
            exists = p.exists()
        except OSError as e:
            out['reason'] = 'read_error'
            out['message'] = f'检查二维码文件失败：{e}'
            return out

        if not exists:
            out['reason'] = 'no_file'
            out['message'] = 'NapCat 还没有生成二维码（可点「刷新二维码」主动请求）'
            return out

        # ---- 读文件 + 判断年龄 ----
        try:
            st = p.stat()
            age = time.time() - st.st_mtime
            out['age'] = age

            if max_age > 0 and age > max_age:
                out['reason'] = 'stale'
                out['message'] = (f'二维码已过期（{age:.0f} 秒前生成，'
                                  f'上限 {max_age:.0f} 秒）')
                return out

            data = p.read_bytes()
            if not data:
                out['reason'] = 'read_error'
                out['message'] = '二维码文件是空的（可能正在写入）'
                return out

            out['ok'] = True
            out['data'] = data
            out['reason'] = 'ok'
            out['message'] = f'就绪（{age:.0f} 秒前生成）'
            return out
        except OSError as e:
            out['reason'] = 'read_error'
            out['message'] = f'读取二维码文件失败：{e}'
            return out


# ---------- 登录状态常量 ----------

PHASE_CHECKING = 'checking'            # 正在检查
PHASE_STARTING = 'starting'            # 正在启动 NapCat
PHASE_OFFLINE = 'offline'              # 服务未连上
PHASE_GENERATING = 'generating_qrcode'  # 正在生成二维码
PHASE_WAITING = 'waiting_qrcode'       # 等待扫码
PHASE_SCANNED = 'qrcode_scanned'       # 已扫码，待确认
PHASE_INITIALIZING = 'initializing'    # 登录成功，初始化中
PHASE_READY = 'ready'                  # 完全就绪
PHASE_RECONNECTING = 'reconnecting'    # 登录服务重启中
PHASE_CANCELLED = 'cancelled'          # 用户取消
PHASE_ERROR = 'error'                  # 出错
PHASE_UNKNOWN = 'unknown'              # 未知状态

# 状态 → 中文说明（界面直接用）
PHASE_TEXT = {
    PHASE_CHECKING: '正在检查服务状态...',
    PHASE_STARTING: '正在启动 NapCat...',
    PHASE_OFFLINE: 'NapCat 服务未连上',
    PHASE_GENERATING: '正在生成二维码...',
    PHASE_WAITING: '请用手机 QQ 扫码登录',
    PHASE_SCANNED: '已扫码，请在手机上确认',
    PHASE_INITIALIZING: '登录成功，正在初始化...',
    PHASE_READY: '已登录',
    PHASE_RECONNECTING: '登录服务重启中...',
    PHASE_CANCELLED: '已取消',
    PHASE_ERROR: '出错了',
    PHASE_UNKNOWN: '等待中...',
}


class NapCatWebUI:
    """NapCat WebUI API 客户端。

    与 OneBot（3000 端口）的关键区别：WebUI（6099）**在登录之前就可访问**，
    所以它是「检查是否已登录 / 获取二维码」的唯一通道。

    Credential 有效期 1 小时，内部自动重新登录。
    """

    def __init__(self, config: Config, log: Callable[[str], None] | None = None):
        self.config = config
        self._log = log or (lambda m: None)
        self._cred: str | None = None
        self._cred_time: float = 0.0

    @property
    def base(self) -> str:
        return str(self.config.get('napcat_webui',
                                   'http://127.0.0.1:6099')).rstrip('/')

    # ---- 底层 ----

    def _request(self, path: str, method: str = 'POST',
                 payload: dict | None = None, timeout: int = 10,
                 with_auth: bool = True) -> dict:
        url = f'{self.base}{path}'
        data = json.dumps(payload or {}).encode()
        headers = {'Content-Type': 'application/json'}
        if with_auth:
            cred = self._credential()
            if not cred:
                raise NapCatError('WebUI 鉴权失败（无法获取 token）')
            headers['Authorization'] = f'Bearer {cred}'

        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode(errors='replace')
        except urllib.error.HTTPError as e:
            raise NapCatError(f'{path} HTTP {e.code}') from e
        except Exception as e:
            raise NapCatError(f'{path} 请求失败: {type(e).__name__}') from e

        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            raise NapCatError(f'{path} 返回非 JSON') from None
        code = d.get('code')
        if code not in (0, None):
            raise NapCatError(f'{path} 失败: {d.get("message") or code}')
        return d.get('data') or {}

    def _read_token(self) -> str | None:
        p = Path(self.config.get('napcat_dir', '')) / 'config' / 'webui.json'
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding='utf-8-sig'))
            return str(d.get('token') or '').strip() or None
        except Exception:
            return None

    def _credential(self, force: bool = False) -> str | None:
        """获取（或刷新）WebUI 凭证。"""
        now = time.time()
        if not force and self._cred and (now - self._cred_time) < 3300:
            return self._cred

        token = self._read_token()
        if not token:
            return None
        h = hashlib.sha256(f'{token}.napcat'.encode()).hexdigest()
        try:
            data = self._request('/api/auth/login', 'POST', {'hash': h},
                                 timeout=10, with_auth=False)
        except NapCatError:
            return None
        cred = data.get('Credential') or data.get('credential')
        if cred:
            self._cred = str(cred)
            self._cred_time = now
        return self._cred

    # ---- 对外接口 ----

    def is_available(self, timeout: int = 3) -> bool:
        """WebUI 是否可达（登录前后都可判断）。

        用根路径探测即可 —— 任何 HTTP 响应（含 401/404）都说明服务在跑。
        """
        try:
            with urllib.request.urlopen(self.base, timeout=timeout) as r:
                return r.status < 500
        except urllib.error.HTTPError as e:
            return e.code < 500
        except Exception:
            return False

    def login_status(self, raise_errors: bool = False) -> dict:
        """查询登录状态（含 loginPhase / 二维码 URL）。

        这是引导流程的核心接口。

        Args:
            raise_errors: False（默认，向后兼容）时任何失败都返回空字典；
                          True 时抛出 NapCatError，让调用方能向用户显示
                          具体原因（鉴权失败 / 端口不通 / 返回异常）。
                          二维码弹窗用 True，避免「静默卡死」。

        Raises:
            NapCatError: 仅当 raise_errors=True 且查询失败时
        """
        last_err: Exception | None = None

        try:
            return self._request('/api/QQLogin/CheckLoginStatus', 'POST',
                                 {}, timeout=8) or {}
        except NapCatError as e:
            last_err = e
            # 凭证可能过期，强刷一次再试
            if self._credential(force=True):
                try:
                    return self._request('/api/QQLogin/CheckLoginStatus',
                                         'POST', {}, timeout=8) or {}
                except NapCatError as e2:
                    last_err = e2

        if raise_errors:
            raise NapCatError(str(last_err) if last_err else '查询登录状态失败')
        return {}

    def qrcode_url(self) -> str:
        """获取当前二维码的 URL 字符串。"""
        try:
            d = self._request('/api/QQLogin/GetQQLoginQrcode', 'POST',
                              {}, timeout=8)
            return str(d.get('qrcode') or '')
        except NapCatError:
            return ''

    def refresh_qrcode(self) -> dict:
        """刷新二维码（内部最多等 10 秒）。"""
        try:
            return self._request('/api/QQLogin/RefreshQRcode', 'POST',
                                 {}, timeout=20) or {}
        except NapCatError as e:
            return {'error': str(e)}

    def login_info(self) -> dict:
        """当前登录信息 {uin, nick, online}。"""
        try:
            d = self._request('/api/QQLogin/GetQQLoginInfo', 'POST',
                              {}, timeout=8)
            return {
                'uin': str(d.get('uin') or ''),
                'nickname': str(d.get('nick') or ''),
                'online': bool(d.get('online')),
            }
        except NapCatError:
            return {}

    def quick_login(self, uin: str) -> bool:
        """触发指定账号的快速登录。"""
        try:
            self._request('/api/QQLogin/SetQuickLogin', 'POST',
                          {'uin': str(uin)}, timeout=15)
            return True
        except NapCatError:
            return False

    def restart(self) -> bool:
        """重启 NapCat 进程（比 kill+start 更优雅）。"""
        try:
            self._request('/api/QQLogin/RestartNapCat', 'POST', {},
                          timeout=15)
            return True
        except NapCatError:
            return False


# ---------- 时间工具 ----------

def period_bounds(label_day: dt.date, day_start_hour: int = 12) -> tuple[float, float]:
    """日期标签 → [当天 day_start_hour, 次日 day_start_hour) 时间戳。"""
    start = dt.datetime.combine(label_day, dt.time(hour=int(day_start_hour)))
    return start.timestamp(), (start + dt.timedelta(days=1)).timestamp()


def latest_complete_day(day_start_hour: int = 12,
                        now: dt.datetime | None = None) -> dt.date:
    """最近一个已结束的完整周期对应的日期标签。"""
    now = now or dt.datetime.now()
    return (now - dt.timedelta(hours=int(day_start_hour))).date() - dt.timedelta(days=1)


def date_tag(day: dt.date) -> str:
    """日期 → 归档标签（261012 形式）。"""
    return day.strftime('%y%m%d')
