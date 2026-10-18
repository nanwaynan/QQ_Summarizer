"""二维码登录对话框

显示 NapCat 生成的登录二维码，并实时反映登录状态：
- 等扫码 → 显示二维码
- 已扫码 → 提示「请在手机上确认」
- 二维码过期 → 自动请求刷新
- 登录成功 → 自动关闭

设计要点（针对「一直卡在正在获取二维码...」的修复）：

1. **主动要码**：打开时如果 NapCat 还没有二维码文件，会自动调用
   RefreshQRcode 请求生成，而不是被动干等。（旧实现只等文件，永远等不到）

2. **分层超时**：15 秒内没拿到码就明确报错，不再无限 loading。

3. **错误透出**：所有失败原因（端口不通 / 鉴权失败 / 文件缺失 / 已过期）
   都显示到界面上，同时写进运行日志。旧实现把这些全部吞成空字典。

4. **不阻塞 UI**：HTTP 请求全部放在 QrcodePollWorker 后台线程执行。
   旧实现直接在 QTimer 回调里发请求，最坏会冻结界面 20+ 秒。

5. **可取消**：关闭弹窗会请求 worker 停止，不再继续发请求。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, MessageBoxBase, PushButton, SubtitleLabel,
)

from ...config import Config
from ...core.napcat import (
    PHASE_ERROR, PHASE_GENERATING, PHASE_INITIALIZING, PHASE_READY,
    PHASE_RECONNECTING, PHASE_SCANNED, PHASE_WAITING, NapCatError,
    NapCatService,
)

QR_SIZE = 240

# 首次拿到二维码的时限（秒）——超时后明确报错
FIRST_QRCODE_TIMEOUT = 15.0
# 弹窗总存活时限（秒）——超过后提示用户重开
TOTAL_TIMEOUT = 120.0
# 轮询间隔（毫秒）
POLL_INTERVAL = 2000


class QrcodeLoginDialog(MessageBoxBase):
    """二维码登录对话框。

    Signals:
        logged_in(object) : 登录成功，携带账号信息
        cancelled()       : 用户取消
    """

    logged_in = Signal(object)
    cancelled = Signal()

    def __init__(self, config: Config, parent: QWidget | None = None,
                 log: Optional[Callable[[str, str], None]] = None):
        """Args:
            log: 可选日志回调 (级别, 文本)，用于把失败原因写进运行日志。
        """
        # MessageBoxBase 要求 parent 非空（它按 parent 尺寸铺遮罩层）
        if parent is None:
            from PySide6.QtWidgets import QApplication
            parent = QApplication.activeWindow()
        super().__init__(parent)

        self.config = config
        self._log_sink = log or (lambda lv, msg: None)
        self._svc = NapCatService(config)
        self._last_hash: int = 0
        self._finished = False
        self._worker = None
        self._started_at = time.time()
        self._got_qrcode = False
        self._error_shown = False
        self._pending_refresh = False
        # 已因超时/致命错误终止轮询：此时忽略迟到的 worker 回调，
        # 避免它把超时提示又覆盖回一般错误
        self._halted = False

        self._build_ui()

        # 轮询定时器：只负责调度，真正的 HTTP 在 worker 里
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_INTERVAL)
        # 立即来一次（不等 2 秒）
        QTimer.singleShot(100, self._tick)

    # ---------- 日志 ----------

    def _log(self, level: str, msg: str) -> None:
        try:
            self._log_sink(level, msg)
        except Exception:
            pass

    # ---------- UI ----------

    def _build_ui(self) -> None:
        title = SubtitleLabel('扫码登录 QQ')
        self.viewLayout.addWidget(title)

        hint = CaptionLabel(
            'NapCat 需要用你的 QQ 小号读取群消息。\n'
            '请用手机 QQ 扫描下方二维码登录（登录态会保存在本机，之后可免扫码）。')
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        # 二维码显示区
        self.qr_label = BodyLabel('正在获取二维码...')
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setWordWrap(True)
        self.qr_label.setMinimumSize(QR_SIZE + 20, QR_SIZE + 20)
        self.qr_label.setStyleSheet(
            'QLabel { border: 1px solid rgba(128,128,128,0.3);'
            ' border-radius: 8px; padding: 8px; }')
        self.viewLayout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignCenter)

        # 状态文字（失败原因、进度提示）
        self.status = BodyLabel('准备中...')
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(40)     # 留出两行，避免被截断
        self.viewLayout.addWidget(self.status)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_refresh = PushButton('刷新二维码')
        self.btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch(1)
        self.viewLayout.addLayout(btn_row)

        # 底部：取消
        self.yesButton.setText('已完成登录')
        self.yesButton.setVisible(False)   # 自动检测成功，不用手点
        self.cancelButton.setText('取消')

    # ---------- 轮询调度 ----------

    def _tick(self) -> None:
        """调度一轮后台查询（不阻塞 UI）。"""
        if self._finished:
            return

        # 总超时
        elapsed = time.time() - self._started_at
        if elapsed > TOTAL_TIMEOUT:
            self._timer.stop()
            self._fail(
                f'登录窗口已打开 {elapsed:.0f} 秒仍未完成登录。\n'
                '可以关闭窗口后重新点「扫码登录」。')
            return

        # 首次拿码超时
        if not self._got_qrcode and elapsed > FIRST_QRCODE_TIMEOUT:
            self._timer.stop()
            self._fail(
                f'获取二维码超时（{FIRST_QRCODE_TIMEOUT:.0f} 秒）。\n'
                '请检查 NapCat 状态，或点「刷新二维码」重试。')
            return

        # 上一轮还没结束就跳过，避免堆积线程
        if self._worker is not None and self._worker.isRunning():
            return

        from ..workers import QrcodePollWorker

        w = QrcodePollWorker(self.config)
        w.tick_done.connect(self._on_tick_done)
        w.tick_failed.connect(self._on_tick_failed)
        w.start()
        self._worker = w

    # ---------- Worker 回调 ----------

    def _on_tick_failed(self, msg: str) -> None:
        if self._finished or self._halted:
            return
        self._log('ERROR', f'[扫码登录] 轮询异常：{msg}')
        self.status.setText(f'⚠ 查询失败：{msg[:120]}')
        self.status.setTextColor('#c42b1c', '#ff99a4')

    def _on_tick_done(self, data: object) -> None:
        if self._finished or self._halted:
            return
        d = data if isinstance(data, dict) else {}
        st = d.get('status') or {}
        qr = d.get('qr') or {}
        api_err = str(d.get('error') or '').strip()
        refresh_err = str(d.get('refresh_error') or '').strip()

        # ---- 1. 登录成功判断 ----
        phase = str(st.get('loginPhase') or '').strip()
        is_login = bool(st.get('isLogin'))
        if is_login and phase in (PHASE_READY, PHASE_INITIALIZING, ''):
            info = self._webui_login_info()
            self._finish_ok(info)
            return

        # ---- 2. 服务层错误（端口不通 / 鉴权失败）----
        # 注意顺序：如果本地已经有二维码文件，即使 API 鉴权失败，
        # 也应该先把二维码显示出来（不少 NapCat 版本鉴权有问题但内核照常写图）。
        qr_ok = bool(qr.get('ok') and qr.get('data'))
        if api_err and not qr_ok:
            self._log('ERROR', f'[扫码登录] 无法查询登录状态：{api_err}')
            self._show_error(
                '无法连接 NapCat 服务',
                api_err,
                hint='请确认 NapCat 正在运行，且「设置」里的 NapCat 目录正确。')

        # ---- 3. 刷新请求失败 ----
        if refresh_err:
            self._log('WARN', f'[扫码登录] 请求刷新二维码失败：{refresh_err}')
            self.status.setText(f'⚠ 请求刷新失败：{refresh_err[:100]}')
            self.status.setTextColor('#c42b1c', '#ff99a4')

        # ---- 4. 二维码可用 → 显示 ----
        if qr.get('ok') and qr.get('data'):
            self._show_qrcode(qr['data'])
            self._got_qrcode = True
            self._error_shown = False
        else:
            reason = str(qr.get('reason') or '')
            msg = str(qr.get('message') or '')

            if reason in ('no_file', 'stale'):
                # worker 已经在尝试主动刷新了，这里只提示进度
                if d.get('refreshed'):
                    self.status.setText('已请求 NapCat 生成二维码，请稍候...')
                    self.status.setTextColor('#000000', '#ffffff')
                    self._log('INFO', '[扫码登录] 已请求 NapCat 刷新二维码')
                else:
                    self.status.setText(msg or '等待 NapCat 生成二维码...')
                    self.status.setTextColor('#9d5d00', '#ffd166')
            elif reason == 'no_dir':
                self._show_error('NapCat 目录无效', msg,
                                 hint='请在「设置」页确认 NapCat 目录。')
            elif reason == 'read_error':
                self.status.setText(msg or '读取二维码失败')
                self.status.setTextColor('#c42b1c', '#ff99a4')
            elif not api_err:
                # 没有 API 错误但也没有二维码，给个中性提示
                self.status.setText(msg or '等待二维码...')

        # ---- 5. 阶段文案 ----
        # 只在「没有二维码」且「没有更具体的文件状态提示」时，才用阶段文案覆盖。
        # 否则会把上面「还没有生成 / 已过期」这类具体原因冲掉（旧实现的毛病之一）。
        reason_now = str(qr.get('reason') or '')
        has_specific = reason_now in ('no_file', 'stale', 'no_dir', 'read_error')
        if not api_err and not qr.get('ok') and not has_specific:
            if phase == PHASE_WAITING:
                self.status.setText('请用手机 QQ 扫码')
            elif phase == PHASE_SCANNED:
                self.status.setText('已扫码，请在手机上点击确认')
                self.status.setTextColor('#9d5d00', '#ffd166')
            elif phase == PHASE_GENERATING:
                self.status.setText('NapCat 正在生成二维码...')
            elif phase == PHASE_RECONNECTING:
                self.status.setText('登录服务重启中，请稍候...')
            elif phase == PHASE_ERROR:
                err = str(st.get('loginError') or '').strip()
                self.status.setText(f'⚠ {err or "登录出错"}')
                self.status.setTextColor('#c42b1c', '#ff99a4')

        # 有图时按相位更新文字
        if qr.get('ok'):
            if phase == PHASE_SCANNED:
                self.status.setText('已扫码，请在手机上点击确认')
                self.status.setTextColor('#9d5d00', '#ffd166')
            elif phase == PHASE_INITIALIZING:
                self.status.setText('登录成功，正在初始化...')
                self.status.setTextColor('#107c10', '#6ccb5f')
            else:
                self.status.setText('请用手机 QQ 扫码')
                self.status.setTextColor('#000000', '#ffffff')

    def _webui_login_info(self) -> dict:
        """取登录信息（失败不抛，回退到 OneBot）。"""
        try:
            info = self._svc.webui().login_info() or {}
            if info:
                return info
        except Exception:
            pass
        try:
            from ...core.napcat import OneBotClient
            return OneBotClient(self.config).login_info() or {}
        except Exception:
            return {}

    # ---------- 显示 ----------

    def _show_qrcode(self, data: bytes) -> None:
        h = hash(data)
        if h == self._last_hash:
            return

        pix = QPixmap()
        if not pix.loadFromData(data):
            # 文件可能正在被写入，下一轮再试
            return

        self._last_hash = h
        pix = pix.scaled(QR_SIZE, QR_SIZE,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        self.qr_label.setPixmap(pix)
        self.qr_label.setText('')
        self.qr_label.setStyleSheet(
            'QLabel { border: 1px solid rgba(128,128,128,0.3);'
            ' border-radius: 8px; padding: 8px; }')

    def _show_error(self, title: str, detail: str, hint: str = '') -> None:
        """把错误显示在二维码区域（替代「正在获取二维码...」）。"""
        if self._error_shown and self._got_qrcode:
            return
        self._error_shown = True

        lines = [f'⚠ {title}']
        if detail:
            lines.append(detail[:180])
        if hint:
            lines.append(hint)
        self.qr_label.setText('\n\n'.join(lines))
        self.qr_label.setStyleSheet(
            'QLabel { border: 1px solid rgba(196,43,28,0.45);'
            ' border-radius: 8px; padding: 12px;'
            ' color: #c42b1c; }')

    def _fail(self, message: str) -> None:
        """终止轮询并显示明确错误（不再是无限 loading）。"""
        if self._finished:
            return
        self._halted = True          # 忽略迟到的 worker 回调
        self._log('ERROR', f'[扫码登录] {message.replace(chr(10), " ")}')
        self.qr_label.setText(f'⚠ {message}')
        self.qr_label.setStyleSheet(
            'QLabel { border: 1px solid rgba(196,43,28,0.45);'
            ' border-radius: 8px; padding: 12px;'
            ' color: #c42b1c; }')
        self.status.setText('可点「刷新二维码」重试，或关闭窗口后重开。')
        self.status.setTextColor('#9d5d00', '#ffd166')
        # 保留弹窗，让用户能看到原因并选择重试/取消

    # ---------- 操作 ----------

    def _on_refresh(self) -> None:
        """用户手点刷新：在后台请求，不阻塞 UI。"""
        if self._finished:
            return

        self.status.setText('正在请求刷新二维码...')
        self.status.setTextColor('#000000', '#ffffff')
        self._last_hash = 0
        self._got_qrcode = False
        self._error_shown = False
        self._halted = False          # 允许重试
        self._started_at = time.time()   # 重置超时计时

        # 清掉错误态，回到等待态
        self.qr_label.setPixmap(QPixmap())
        self.qr_label.setText('正在获取二维码...')
        self.qr_label.setStyleSheet(
            'QLabel { border: 1px solid rgba(128,128,128,0.3);'
            ' border-radius: 8px; padding: 8px; }')

        from ..workers import QrcodePollWorker

        w = QrcodePollWorker(self.config)
        w.tick_done.connect(self._on_refresh_done)
        w.tick_failed.connect(self._on_tick_failed)
        w.start()
        self._worker = w

        # 定时器继续跑（如果它因超时停了，重新启动）
        if not self._timer.isActive():
            self._timer.start(POLL_INTERVAL)

    def _on_refresh_done(self, data: object) -> None:
        """刷新请求完成后，交给常规处理逻辑。"""
        if self._finished:
            return
        d = data if isinstance(data, dict) else {}
        if d.get('refreshed'):
            if d.get('refresh_error'):
                self.status.setText(f'⚠ 刷新失败：{str(d["refresh_error"])[:100]}')
                self.status.setTextColor('#c42b1c', '#ff99a4')
            else:
                self._log('INFO', '[扫码登录] 刷新请求已提交')
        # 复用常规处理（会显示二维码或给出原因）
        self._on_tick_done(data)

    def _finish_ok(self, info: dict) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        nick = info.get('nickname', '')
        uin = info.get('user_id') or info.get('uin') or ''
        self.status.setText(f'✓ 登录成功：{nick} ({uin})')
        self.status.setTextColor('#107c10', '#6ccb5f')
        self._log('INFO', f'[扫码登录] 登录成功：{nick} ({uin})')
        self.logged_in.emit(info)
        QTimer.singleShot(600, self.close)

    # ---------- 关闭 ----------

    def reject(self) -> None:
        """取消：停定时器 + 请求 worker 停止。"""
        if not self._finished:
            self._finished = True
            self._timer.stop()
            # 通知后台线程尽快退出（它每一轮开始/结束都会检查）
            w = self._worker
            if w is not None:
                try:
                    if w.isRunning():
                        w.requestInterruption()
                        w.cancel()
                except Exception:
                    pass
            self._log('INFO', '[扫码登录] 用户取消了登录')
            self.cancelled.emit()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        """关闭窗口时确保定时器与线程都停下。"""
        try:
            self._timer.stop()
            w = self._worker
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.cancel()
        except Exception:
            pass
        super().closeEvent(event)
