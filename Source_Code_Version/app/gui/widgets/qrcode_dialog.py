"""二维码登录对话框

显示 NapCat 生成的登录二维码，并实时反映登录状态：
- 等扫码 → 显示二维码
- 已扫码 → 提示「请在手机上确认」
- 二维码过期 → 自动刷新
- 登录成功 → 自动关闭
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, MessageBoxBase, PrimaryPushButton, PushButton,
    SubtitleLabel,
)

from ...config import Config
from ...core.napcat import (
    PHASE_ERROR, PHASE_GENERATING, PHASE_INITIALIZING, PHASE_READY,
    PHASE_RECONNECTING, PHASE_SCANNED, PHASE_WAITING, NapCatService,
)

QR_SIZE = 240


class QrcodeLoginDialog(MessageBoxBase):
    """二维码登录对话框。

    Signals:
        logged_in(object) : 登录成功，携带账号信息
        cancelled()       : 用户取消
    """

    logged_in = Signal(object)
    cancelled = Signal()

    def __init__(self, config: Config, parent: QWidget | None = None):
        # MessageBoxBase 要求 parent 非空（它按 parent 尺寸铺遮罩层）
        if parent is None:
            from PySide6.QtWidgets import QApplication
            parent = QApplication.activeWindow()
        super().__init__(parent)
        self.config = config
        self._svc = NapCatService(config)
        self._webui = self._svc.webui()
        self._last_hash: int = 0
        self._finished = False
        self._poll_count = 0

        self._build_ui()

        # 轮询：每 2 秒查一次登录状态 + 检查二维码是否更新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(2000)
        QTimer.singleShot(300, self._tick)

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
        self.qr_label.setMinimumSize(QR_SIZE + 20, QR_SIZE + 20)
        self.qr_label.setStyleSheet(
            'QLabel { border: 1px solid rgba(128,128,128,0.3);'
            ' border-radius: 8px; padding: 8px; }')
        self.viewLayout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignCenter)

        # 状态文字
        self.status = BodyLabel('准备中...')
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

    # ---------- 轮询 ----------

    def _tick(self) -> None:
        if self._finished:
            return
        self._poll_count += 1

        try:
            st = self._webui.login_status()
        except Exception:
            st = {}

        phase = str(st.get('loginPhase') or '').strip()
        is_login = bool(st.get('isLogin'))
        err = str(st.get('loginError') or '').strip()

        # 登录成功
        if is_login and phase in (PHASE_READY, PHASE_INITIALIZING, ''):
            info = self._webui.login_info() or {}
            if not info:
                from ...core.napcat import OneBotClient
                info = OneBotClient(self.config).login_info() or {}
            self._finish_ok(info)
            return

        # 状态文案
        if err:
            self.status.setText(f'⚠ {err}')
            self.status.setTextColor('#c42b1c', '#ff99a4')
        elif phase == PHASE_SCANNED:
            self.status.setText('已扫码，请在手机上点击确认')
            self.status.setTextColor('#9d5d00', '#ffd166')
        elif phase == PHASE_WAITING:
            self.status.setText('请用手机 QQ 扫码')
            self.status.setTextColor('#000000', '#ffffff')
        elif phase == PHASE_GENERATING:
            self.status.setText('正在生成二维码...')
        elif phase == PHASE_RECONNECTING:
            self.status.setText('登录服务重启中，请稍候...')
        elif phase == PHASE_INITIALIZING:
            self.status.setText('登录成功，正在初始化...')
        elif phase == PHASE_ERROR:
            self.status.setText(f'⚠ {err or "出错了"}')
            self.status.setTextColor('#c42b1c', '#ff99a4')

        # 更新二维码图片（有变化才重绘）
        self._update_qrcode()

    def _update_qrcode(self) -> None:
        data = self._svc.qrcode_bytes(max_age=300)
        if not data:
            # 30 次轮询（约 1 分钟）还没图，提示可能有问题
            if self._poll_count > 15 and '获取' in self.status.text():
                self.status.setText('未能获取二维码，可点「刷新二维码」重试')
            return

        h = hash(data)
        if h == self._last_hash:
            return
        self._last_hash = h

        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        pix = pix.scaled(QR_SIZE, QR_SIZE,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        self.qr_label.setPixmap(pix)
        self.qr_label.setText('')

    # ---------- 操作 ----------

    def _on_refresh(self) -> None:
        self.status.setText('正在刷新二维码...')
        self.status.setTextColor('#000000', '#ffffff')
        self._last_hash = 0
        self._poll_count = 0
        try:
            self._webui.refresh_qrcode()
        except Exception as e:
            self.status.setText(f'刷新失败：{str(e)[:60]}')
        QTimer.singleShot(1500, self._tick)

    def _finish_ok(self, info: dict) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        nick = info.get('nickname', '')
        uin = info.get('user_id') or info.get('uin') or ''
        self.status.setText(f'✓ 登录成功：{nick} ({uin})')
        self.status.setTextColor('#107c10', '#6ccb5f')
        self.logged_in.emit(info)
        QTimer.singleShot(600, self.close)

    # ---------- 关闭 ----------

    def reject(self) -> None:
        if not self._finished:
            self._finished = True
            self._timer.stop()
            self.cancelled.emit()
        super().reject()
