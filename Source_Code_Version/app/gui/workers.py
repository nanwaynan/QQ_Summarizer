"""工作线程

把耗时的核心操作放进 QThread，避免阻塞 UI。

设计：
- Worker 基类提供 signal / cancel 标志 / 日志转发
- 具体 Worker 继承它实现 run()
"""

from __future__ import annotations

import datetime as dt
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Config
from ..core.napcat import OneBotClient, NapCatService
from ..core.ai import AIClient
from ..core.summarize import SummarizeTask
from ..logger import Logger, LogRecord


class WorkerBase(QThread):
    """工作线程基类。

    Signals:
        log(str, str)        : (级别, 文本)  —— 供界面显示
        progress(str, float) : (阶段说明, 0-1)
        finished_ok(object)  : 结果对象
        failed(str)          : 错误文本
        done()               : 无论成败都会发出（用于恢复 UI 状态）
    """

    log = Signal(str, str)
    progress = Signal(str, float)
    finished_ok = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(self, logger: Logger | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.logger = logger
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消（线程会在下一个检查点退出）。"""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def should_cancel(self) -> bool:
        return self._cancelled

    # 内部：把日志转发到 signal
    def _emit_log(self, level: str, text: str) -> None:
        self.log.emit(level, text)

    def run(self) -> None:  # noqa: D102  （子类实现）
        raise NotImplementedError


class SummarizeWorker(WorkerBase):
    """执行一次总结任务。"""

    def __init__(self, config: Config, logger: Logger,
                 start_ts: float, end_ts: float, label: dt.date,
                 group_id: str, group_name: str,
                 parent: QObject | None = None):
        super().__init__(logger, parent)
        self.config = config
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.label = label
        self.group_id = group_id
        self.group_name = group_name

    def run(self) -> None:
        sink_added = False

        def _sink(rec: LogRecord) -> None:
            self.log.emit(rec.level, rec.text)

        try:
            if self.logger:
                self.logger.add_sink(_sink)
                sink_added = True

            task = SummarizeTask(
                self.config, self.logger,
                should_cancel=self.should_cancel,
                on_progress=lambda msg, pct: self.progress.emit(msg, pct),
            )
            result = task.run(
                self.start_ts, self.end_ts, self.label,
                self.group_id, self.group_name)
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(f'{type(e).__name__}: {e}')
            self.log.emit('ERROR', traceback.format_exc()[-1200:])
        finally:
            if self.logger and sink_added:
                self.logger.remove_sink(_sink)
            self.done.emit()


class TestAIWorker(WorkerBase):
    """测试 AI 连接。"""

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config

    def run(self) -> None:
        try:
            ai = AIClient(self.config, lambda m: self.log.emit('INFO', m))
            r = ai.test_connection()
            self.finished_ok.emit(r)
        except Exception as e:
            self.failed.emit(f'{type(e).__name__}: {e}')
        finally:
            self.done.emit()


class LoadGroupsWorker(WorkerBase):
    """加载群列表（含当前登录信息）。"""

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config

    def run(self) -> None:
        try:
            client = OneBotClient(self.config,
                                  lambda m: self.log.emit('INFO', m))
            info = client.login_info()
            groups = client.group_list(no_cache=True)
            self.finished_ok.emit({'login': info, 'groups': groups})
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self.done.emit()


class LoadAccountsWorker(WorkerBase):
    """加载本机可用账号。"""

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config

    def run(self) -> None:
        try:
            svc = NapCatService(self.config,
                                lambda m: self.log.emit('INFO', m))
            accounts = svc.quick_login_list()
            self.finished_ok.emit(accounts)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self.done.emit()


class ServiceCheckWorker(WorkerBase):
    """检查 NapCat 服务状态（三态：未运行 / 等扫码 / 已登录）。"""

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config

    def run(self) -> None:
        """返回 {'state': 'running'|'waiting_login'|'stopped', 'info': {...}, 'phase': str}"""
        out = {'state': 'stopped', 'info': {}, 'phase': ''}
        try:
            svc = NapCatService(self.config)

            # 先看 OneBot（3000）——能通说明已登录且就绪
            client = OneBotClient(self.config)
            if client.is_online(timeout=4):
                out['state'] = 'running'
                out['info'] = client.login_info() or {}
                out['phase'] = 'ready'
                return

            # 再看 WebUI（6099）——可达说明 NapCat 在跑，但可能没登录
            ui = svc.webui()
            if ui.is_available(timeout=4):
                st = ui.login_status()
                phase = str(st.get('loginPhase') or '').strip()
                out['phase'] = phase
                if st.get('isLogin'):
                    # 已登录但 OneBot 还没就绪（初始化中）
                    out['state'] = 'running'
                    out['info'] = ui.login_info() or {}
                else:
                    out['state'] = 'waiting_login'
                return

            # 两者都不通 → 未运行
            out['state'] = 'stopped'
        except Exception as e:
            out['error'] = f'{type(e).__name__}: {e}'
        finally:
            self.finished_ok.emit(out)
            self.done.emit()


class StartServiceWorker(WorkerBase):
    """启动 NapCat 并等待登录（自动检测 → 自动启动 → 扫码引导）。

    用 WebUI 的 loginPhase 状态机精确感知进度，并通过 phase 信号上报给界面。
    """

    # (phase, 说明文字, data)
    phase = Signal(str, str, object)

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config

    def run(self) -> None:
        try:
            from ..core.napcat import PHASE_TEXT

            svc = NapCatService(self.config,
                                lambda m: self.log.emit('INFO', m))

            def _on_phase(phase: str, data: dict) -> None:
                text = PHASE_TEXT.get(phase, phase)
                # 带上细节信息，便于界面提示
                detail = dict(data) if isinstance(data, dict) else {}
                if phase == 'starting' and detail.get('waited'):
                    text = f'正在启动 NapCat（{detail["waited"]} 秒）...'
                    self.progress.emit(text, min(0.4, detail['waited'] / 60))
                elif phase == 'waiting_qrcode':
                    self.progress.emit(text, 0.5)
                elif phase == 'qrcode_scanned':
                    self.progress.emit(text, 0.7)
                elif phase == 'initializing':
                    self.progress.emit(text, 0.85)
                elif phase == 'ready':
                    self.progress.emit(text, 1.0)
                self.phase.emit(phase, text, detail)

            result = svc.ensure_running(
                on_phase=_on_phase,
                should_cancel=self.should_cancel,
                timeout=240,
                poll_interval=2.0,
            )

            if result.get('ok'):
                info = result.get('info') or {}
                self.finished_ok.emit({'online': True, 'info': info})
            elif result.get('phase') == 'cancelled':
                self.finished_ok.emit({'online': False, 'info': {},
                                       'cancelled': True})
            else:
                self.failed.emit(result.get('error') or '启动失败')
        except Exception as e:
            import traceback
            self.failed.emit(f'{type(e).__name__}: {e}')
            self.log.emit('ERROR', traceback.format_exc()[-800:])
        finally:
            self.done.emit()


class QrcodeWorker(WorkerBase):
    """轮询二维码图片，有新图就发信号。

    二维码由 NapCat 内核写入 <napcat_dir>/cache/qrcode.png，
    文件变化即表示有新码（约 2 分钟刷新一次）。
    """

    # 新二维码图片字节
    qrcode_ready = Signal(bytes)
    # 状态文字变化
    status_text = Signal(str)

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config
        self._interval = 1.5

    def run(self) -> None:
        import time as _t
        svc = NapCatService(self.config)
        last_mtime = 0.0
        last_size = 0
        waited = 0.0
        max_wait = 300.0   # 最多轮询 5 分钟

        while waited < max_wait:
            if self.should_cancel():
                self.status_text.emit('已取消')
                break

            p = svc.qrcode_file
            try:
                if p.exists():
                    st = p.stat()
                    if (st.st_mtime, st.st_size) != (last_mtime, last_size):
                        data = p.read_bytes()
                        if data:
                            last_mtime, last_size = st.st_mtime, st.st_size
                            self.qrcode_ready.emit(data)
                            self.status_text.emit('请用手机 QQ 扫码登录')
            except OSError:
                pass

            _t.sleep(self._interval)
            waited += self._interval

        self.done.emit()

    def refresh(self, webui=None) -> None:
        """请求 NapCat 刷新二维码。"""
        try:
            ui = webui or NapCatService(self.config).webui()
            ui.refresh_qrcode()
            self.status_text.emit('正在刷新二维码...')
        except Exception as e:
            self.status_text.emit(f'刷新失败：{str(e)[:60]}')


class QrcodePollWorker(WorkerBase):
    """二维码状态轮询（后台线程版）。

    与上面 QrcodeWorker 的区别：
    - QrcodeWorker 只盯文件变化，不查 WebUI 状态
    - 本类同时查询 WebUI 登录状态 + 二维码文件状态，并在必要时**主动请求刷新**

    放进 QThread 的原因：NapCat WebUI 的 HTTP 请求单次超时 8 秒，
    鉴权失败时内部还会重登重试，最坏会阻塞 20 秒以上。
    放在 GUI 线程里会让弹窗看起来「卡死」，这正是本次修复的核心问题之一。

    Signals:
        tick_done(dict) : 每轮轮询结束，携带本轮完整结果
        tick_failed(str): 本轮出现异常（网络/鉴权），附错误文本
    """

    tick_done = Signal(object)
    tick_failed = Signal(str)

    # 自动请求刷新的最小间隔（秒）——避免每 2 秒打爆 NapCat
    REFRESH_COOLDOWN = 10.0

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config
        self._svc = NapCatService(config)
        self._webui = self._svc.webui()
        self._last_refresh = 0.0
        self._refreshed_once = False

    def run(self) -> None:
        """执行一轮：查状态 → 判断是否需要刷新 → 读二维码。"""
        import time as _t

        out = {
            'status': {}, 'error': '', 'qr': {},
            'refreshed': False, 'refresh_error': '',
        }

        try:
            # ---- 1. 查登录状态（严格模式，错误不吞）----
            try:
                out['status'] = self._webui.login_status(raise_errors=True) or {}
            except Exception as e:
                out['error'] = str(e)[:200]
                # 状态查不到不代表二维码一定读不到（有些版本鉴权坏了但文件还在写），
                # 所以继续往下读文件，但把错误带回去让界面显示
                out['status'] = {}

            if self.should_cancel():
                return

            # ---- 2. 读二维码文件状态 ----
            qr = self._svc.qrcode_state(max_age=300)
            out['qr'] = qr

            # ---- 3. 需要时主动请求刷新 ----
            # 仅在「服务可达」且「没有可用二维码」时刷新，避免服务没起来时瞎试
            need_refresh = (not qr.get('ok')
                            and qr.get('reason') in ('no_file', 'stale'))
            if need_refresh and not out['error']:
                now = _t.time()
                if now - self._last_refresh >= self.REFRESH_COOLDOWN:
                    self._last_refresh = now
                    try:
                        r = self._webui.refresh_qrcode() or {}
                        out['refreshed'] = True
                        if r.get('error'):
                            out['refresh_error'] = str(r['error'])[:200]
                    except Exception as e:
                        out['refreshed'] = True
                        out['refresh_error'] = str(e)[:200]
                    self._refreshed_once = True

            if self.should_cancel():
                return
            self.tick_done.emit(out)

        except Exception as e:
            if not self.should_cancel():
                self.tick_failed.emit(f'{type(e).__name__}: {e}')
        finally:
            self.done.emit()

    def force_refresh(self) -> dict:
        """用户手点「刷新二维码」时调用（同步执行，调用方放后台）。"""
        self._last_refresh = 0.0
        try:
            r = self._webui.refresh_qrcode() or {}
            return {'ok': not r.get('error'), 'error': str(r.get('error') or '')}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


class SwitchAccountWorker(WorkerBase):
    """切换 QQ 账号（会重启 NapCat，约 1-3 分钟）。"""

    def __init__(self, config: Config, uin: str,
                 parent: QObject | None = None):
        super().__init__(None, parent)
        self.config = config
        self.uin = uin

    def run(self) -> None:
        try:
            svc = NapCatService(self.config,
                                lambda m: self.log.emit('INFO', m))

            def _step(msg: str) -> None:
                self.log.emit('INFO', msg)
                self.progress.emit(msg, 0.0)

            def _wait(done_s: int, total_s: int) -> None:
                self.progress.emit(
                    f'等待服务就绪（{done_s}/{total_s} 秒）',
                    min(0.95, done_s / max(1, total_s)))

            ok = svc.switch_account(self.uin, on_step=_step, on_wait=_wait)
            if not ok:
                self.failed.emit('账号切换失败，请检查 QQ 登录窗口')
                return
            client = OneBotClient(self.config)
            self.finished_ok.emit(client.login_info())
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self.done.emit()
