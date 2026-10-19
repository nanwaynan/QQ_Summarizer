"""首页 / 手动总结页

功能：
- 时间段选择（含校验）
- 服务状态显示 + 一键启动
- 开始总结（后台线程 + 实时日志 + 进度）
- 完成后打开输出目录
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon as FIF,
    InfoBar, InfoBarPosition, PlainTextEdit, PrimaryPushButton,
    ProgressBar, PushButton, StrongBodyLabel, SubtitleLabel,
)

from ...config import Config
from ...constants import (
    CARD_MARGINS, CARD_SPACING, PAGE_MARGINS, PAGE_SPACING,
    COLOR_ERROR, COLOR_MUTED, COLOR_SUCCESS, COLOR_WARN, level_color,
)
from ...logger import Logger
from ..widgets.time_range import TimeRangePicker
from ..workers import ServiceCheckWorker, StartServiceWorker, SummarizeWorker


def _mono_font():
    """等宽字体（日志显示用）。"""
    from PySide6.QtGui import QFont
    f = QFont('Cascadia Mono', 10)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


class ManualPage(QWidget):
    """手动总结页。"""

    def __init__(self, config: Config, logger: Logger,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.logger = logger
        self.setObjectName('ManualPage')

        self._summarize_worker = None
        self.service_worker = None
        self._start_worker = None
        self._qr_dialog = None
        self._service_state = 'unknown'
        self._auto_start_pending = False

        self._build_ui()
        self._restore_range()

        # 启动后自动检查服务状态（延迟一点，等界面显示出来）
        # auto_start=True：检测到未运行时会自动启动 NapCat
        QTimer.singleShot(800, lambda: self.check_service(auto_start=True))

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        title = SubtitleLabel('手动总结')
        root.addWidget(title)

        # ---- 服务状态卡片 ----
        self.service_card = CardWidget()
        sc = QHBoxLayout(self.service_card)
        sc.setContentsMargins(*CARD_MARGINS)
        sc.setSpacing(CARD_SPACING)

        self.service_dot = BodyLabel('●')
        self.service_dot.setTextColor(*COLOR_MUTED)
        sc.addWidget(self.service_dot)

        self.service_text = BodyLabel('正在检查服务状态...')
        sc.addWidget(self.service_text)
        sc.addStretch(1)

        self.btn_login = PushButton(FIF.QRCODE, '扫码登录')
        self.btn_login.setVisible(False)
        self.btn_login.clicked.connect(self.scan_login)
        sc.addWidget(self.btn_login)

        self.btn_check = PushButton(FIF.SYNC, '重新检查')
        self.btn_check.clicked.connect(lambda: self.check_service())
        sc.addWidget(self.btn_check)

        self.btn_start_service = PushButton(FIF.POWER_BUTTON, '启动服务')
        self.btn_start_service.clicked.connect(self.start_service)
        sc.addWidget(self.btn_start_service)

        root.addWidget(self.service_card)

        # ---- 时间段选择 ----
        self.range_picker = TimeRangePicker()
        self.range_picker.validChanged.connect(self._on_range_changed)
        root.addWidget(self.range_picker)

        # ---- 操作区 ----
        action = QHBoxLayout()
        action.setSpacing(10)

        self.btn_run = PrimaryPushButton(FIF.PLAY, '开始总结')
        self.btn_run.setMinimumWidth(140)
        self.btn_run.clicked.connect(self.start_summarize)
        action.addWidget(self.btn_run)

        self.btn_cancel = PushButton(FIF.CANCEL, '取消')
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_summarize)
        action.addWidget(self.btn_cancel)

        self.btn_open = PushButton(FIF.FOLDER, '打开输出目录')
        self.btn_open.clicked.connect(self.open_output_dir)
        action.addWidget(self.btn_open)

        action.addStretch(1)
        self.status_label = CaptionLabel('')
        action.addWidget(self.status_label)
        root.addLayout(action)

        # ---- 进度条 ----
        self.progress = ProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.stage_label = CaptionLabel('')
        root.addWidget(self.stage_label)

        # ---- 日志区 ----
        log_header = QHBoxLayout()
        log_header.addWidget(StrongBodyLabel('运行日志'))
        log_header.addStretch(1)
        btn_clear = PushButton(FIF.DELETE, '清空')
        btn_clear.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(btn_clear)
        root.addLayout(log_header)

        self.log_view = PlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(_mono_font())
        root.addWidget(self.log_view, 1)

        self._set_running(False)

    # ---------- 时间段持久化 ----------

    def _restore_range(self) -> None:
        """恢复上次选择的时间段（存到配置）。"""
        saved = self.config.get('last_range')
        if isinstance(saved, dict):
            try:
                s = dt.datetime.fromisoformat(saved['start'])
                e = dt.datetime.fromisoformat(saved['end'])
                # 只在仍然合法时恢复
                now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
                if s < e and e <= now and (e - s).total_seconds() <= 24 * 3600:
                    self.range_picker.set_range(s, e)
                    return
            except Exception:
                pass
        self.range_picker.set_yesterday_period()

    def _save_range(self, start: dt.datetime, end: dt.datetime) -> None:
        self.config.set('last_range', {
            'start': start.isoformat(),
            'end': end.isoformat(),
        })
        try:
            self.config.save()
        except Exception:
            pass

    # ---------- 状态 ----------

    def _set_running(self, running: bool) -> None:
        self.btn_run.setEnabled(not running and self.range_picker.is_valid())
        self.btn_cancel.setEnabled(running)
        self.btn_check.setEnabled(not running)
        self.btn_start_service.setEnabled(not running)
        self.progress.setVisible(running)
        if running:
            self.progress.setRange(0, 100)
        else:
            self.progress.setRange(0, 100)
            self.stage_label.setText('')

    def _on_range_changed(self, valid: bool) -> None:
        if self._summarize_worker is None:
            self.btn_run.setEnabled(valid)

    # ---------- 服务检查 ----------

    def check_service(self, auto_start: bool = False) -> None:
        """检查服务状态。auto_start=True 时，未运行则自动启动。"""
        self.service_text.setText('正在检查服务状态...')
        self._set_dot('unknown')
        self._auto_start_pending = auto_start

        w = ServiceCheckWorker(self.config)
        w.finished_ok.connect(self._on_service_checked)
        w.start()
        self._service_worker = w

    def _set_dot(self, state: str) -> None:
        """设置状态圆点颜色（三态，深浅色自适应）。"""
        colors = {
            'ok': COLOR_SUCCESS,        # 绿：已登录
            'wait': COLOR_WARN,         # 黄：等扫码
            'error': COLOR_ERROR,       # 红：未运行
            'unknown': COLOR_MUTED,     # 灰：检查中
        }
        self.service_dot.setTextColor(*colors.get(state, COLOR_MUTED))

    def _on_service_checked(self, data: object) -> None:
        d = data if isinstance(data, dict) else {}
        state = str(d.get('state') or 'stopped')
        info = d.get('info') or {}
        phase = str(d.get('phase') or '')

        if state == 'running':
            nick = info.get('nickname', '')
            uin = info.get('user_id') or info.get('uin') or ''
            self._set_dot('ok')
            self.service_text.setText(f'服务正常 · 已登录 {nick} ({uin})')
            self.btn_start_service.setVisible(False)
            self.btn_login.setVisible(False)
            self._service_state = 'running'
            return

        if state == 'waiting_login':
            self._set_dot('wait')
            self.service_text.setText('NapCat 已启动，但 QQ 未登录')
            self.btn_start_service.setVisible(False)
            self.btn_login.setVisible(True)     # 提供「扫码登录」
            self._service_state = 'waiting_login'
            return

        # stopped
        self._set_dot('error')
        self.service_text.setText('NapCat 未运行')
        self.btn_start_service.setVisible(True)
        self.btn_login.setVisible(False)
        self._service_state = 'stopped'

        # 自动启动（仅在明确要求时）
        if getattr(self, '_auto_start_pending', False):
            self._auto_start_pending = False
            self._append_log('INFO', '检测到服务未运行，正在自动启动...')
            QTimer.singleShot(300, self.start_service)

    def start_service(self) -> None:
        """启动 NapCat 并等待登录（自动检测 + 扫码引导）。"""
        self.btn_start_service.setEnabled(False)
        self.btn_check.setEnabled(False)
        self.btn_login.setEnabled(False)
        self.status_label.setText('正在启动服务...')
        self._append_log('INFO', '开始启动 NapCat 服务')

        w = StartServiceWorker(self.config)
        self._start_worker = w
        w.log.connect(self._append_log)
        w.progress.connect(self._on_worker_progress)   # 进度条已在用

        def _on_phase(phase: str, text: str, data: object) -> None:
            """状态机回调：等扫码时弹二维码窗。"""
            self.status_label.setText(text)
            d = data if isinstance(data, dict) else {}
            self._set_dot({
                'ready': 'ok',
                'waiting_qrcode': 'wait',
                'qrcode_scanned': 'wait',
                'initializing': 'wait',
                'starting': 'unknown',
                'checking': 'unknown',
                'reconnecting': 'wait',
                'error': 'error',
            }.get(phase, 'unknown'))
            self.service_text.setText(text)

            # 需要扫码 → 弹二维码对话框
            if phase in ('waiting_qrcode', 'qrcode_scanned',
                         'generating_qrcode') and self._qr_dialog is None:
                self._open_qr_dialog()

            if phase == 'error':
                InfoBar.error('启动失败', str(d.get('error') or text)[:250],
                              duration=8000, position=InfoBarPosition.TOP_RIGHT,
                              parent=self)

        w.phase.connect(_on_phase)
        w.finished_ok.connect(self._on_start_finished)
        w.failed.connect(self._on_start_failed)
        w.done.connect(self._on_start_done)
        w.start()

    def _on_start_finished(self, data: object) -> None:
        d = data if isinstance(data, dict) else {}
        if d.get('cancelled'):
            self._append_log('WARN', '启动已取消')
            return
        info = d.get('info') or {}
        self._append_log('INFO', f'服务就绪：{info.get("nickname", "")} '
                                 f'({info.get("user_id", "")})')
        InfoBar.success('服务已就绪',
                        f'已登录 {info.get("nickname", "")}',
                        duration=3000, position=InfoBarPosition.TOP_RIGHT,
                        parent=self)
        # 关闭二维码窗
        if self._qr_dialog is not None:
            try:
                self._qr_dialog.close()
            except Exception:
                pass
            self._qr_dialog = None
        self.check_service()

    def _on_start_failed(self, msg: str) -> None:
        self._append_log('ERROR', msg)
        self._set_dot('error')
        InfoBar.error('启动失败', msg[:250], duration=8000,
                      position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _on_start_done(self) -> None:
        self.btn_start_service.setEnabled(True)
        self.btn_check.setEnabled(True)
        self.btn_login.setEnabled(True)
        self.status_label.setText('')
        self.progress.setVisible(False)

    def _open_qr_dialog(self) -> None:
        """打开二维码登录对话框。"""
        from ..widgets.qrcode_dialog import QrcodeLoginDialog
        if self._qr_dialog is not None:
            return
        try:
            # 传入日志回调：弹窗内部的失败原因（端口不通/鉴权失败/超时）
            # 会同步写进「运行日志」，方便用户复制排查
            dlg = QrcodeLoginDialog(
                self.config, self.window(),
                log=lambda lv, msg: self._append_log(lv, msg))
            dlg.logged_in.connect(self._on_qr_logged_in)
            dlg.cancelled.connect(self._on_qr_cancelled)
            self._qr_dialog = dlg
            dlg.show()
        except Exception as e:
            self._append_log('ERROR', f'二维码窗口打开失败: {e}')

    def _on_qr_logged_in(self, info: object) -> None:
        d = info if isinstance(info, dict) else {}
        nick = d.get('nickname', '')
        uin = d.get('user_id') or d.get('uin') or ''
        self._append_log('INFO', f'扫码登录成功：{nick} ({uin})')
        self._set_dot('ok')
        self.service_text.setText(f'服务正常 · 已登录 {nick} ({uin})')
        self.btn_login.setVisible(False)
        self._service_state = 'running'
        self._qr_dialog = None
        InfoBar.success('登录成功', f'{nick} ({uin})', duration=3000,
                        position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _on_qr_cancelled(self) -> None:
        self._qr_dialog = None
        self._append_log('WARN', '已关闭扫码窗口（服务仍会继续等待登录）')

    def scan_login(self) -> None:
        """手动打开扫码登录窗口（服务已在跑但未登录时用）。"""
        self._open_qr_dialog()

    # ---------- 总结 ----------

    def start_summarize(self) -> None:
        if not self.range_picker.is_valid():
            InfoBar.warning(
                '时间范围有问题', self.range_picker.error_text(),
                duration=3000, position=InfoBarPosition.TOP, parent=self)
            return

        group_id = str(self.config.get('group_id') or '').strip()
        group_name = str(self.config.get('group_name') or '').strip()
        if not group_id:
            InfoBar.warning(
                '未选择群', '请先到「设置」页选择要总结的群聊',
                duration=4000, position=InfoBarPosition.TOP, parent=self)
            return

        errs = self.config.validate()
        if errs:
            InfoBar.warning(
                '配置不完整', '；'.join(errs[:3]),
                duration=5000, position=InfoBarPosition.TOP, parent=self)
            return

        start, end = self.range_picker.values()
        self._save_range(start, end)

        if not group_name:
            group_name = f'群 {group_id}'

        self.log_view.clear()
        self._append_log('INFO', f'开始总结：{start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}')
        self._set_running(True)
        self.progress.setValue(0)
        self.status_label.setText('运行中...')

        w = SummarizeWorker(
            self.config, self.logger,
            start.timestamp(), end.timestamp(),
            start.date(), group_id, group_name)
        w.log.connect(self._append_log)
        w.progress.connect(self._on_worker_progress)
        w.finished_ok.connect(self._on_summarize_done)
        w.failed.connect(self._on_summarize_failed)
        w.done.connect(self._on_worker_done)
        w.start()
        self._summarize_worker = w

    def cancel_summarize(self) -> None:
        if self._summarize_worker is not None:
            self._summarize_worker.cancel()
            self._append_log('WARN', '已请求取消，等待当前步骤结束...')
            self.btn_cancel.setEnabled(False)

    def _on_worker_progress(self, msg: str, pct: float) -> None:
        self.stage_label.setText(msg)
        if pct > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(pct * 100))

    def _on_summarize_done(self, result: object) -> None:
        r = result
        if getattr(r, 'ok', False):
            self.status_label.setText(
                f'完成 · {r.valid_count} 条发言 · {r.image_count} 张图片 · '
                f'{r.elapsed:.0f}s')
            self.progress.setValue(100)
            self._append_log('INFO', f'输出文件：{r.output_file}')
            InfoBar.success(
                '总结完成',
                f'有效发言 {r.valid_count} 条，图片 {r.image_count} 张\n'
                f'已保存到 {r.output_file.parent}',
                duration=6000, position=InfoBarPosition.TOP_RIGHT, parent=self)

            # 图片甄选未生效时（模型不支持视觉），单独给一条警告
            warn = getattr(r, 'image_warning', '')
            if warn:
                InfoBar.warning(
                    '图片甄选未生效',
                    warn[:300],
                    duration=12000, position=InfoBarPosition.TOP_RIGHT, parent=self)

            # 询问是否打开目录
            QTimer.singleShot(800, self.open_output_dir)
        elif getattr(r, 'cancelled', False):
            self.status_label.setText('已取消')
            self._append_log('WARN', '任务已取消')
        else:
            self._on_summarize_failed(getattr(r, 'error', '未知错误'))

    def _on_summarize_failed(self, msg: str) -> None:
        self.status_label.setText('失败')
        self._append_log('ERROR', msg)
        InfoBar.error(
            '总结失败', msg[:300],
            duration=8000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _on_worker_done(self) -> None:
        self._set_running(False)
        self._summarize_worker = None
        self.check_service()

    # ---------- 日志 ----------

    def _append_log(self, level: str, text: str) -> None:
        from qfluentwidgets import isDarkTheme
        color = level_color(level, isDarkTheme())
        stamp = dt.datetime.now().strftime('%H:%M:%S')
        safe = (str(text).replace('&', '&amp;')
                .replace('<', '&lt;').replace('>', '&gt;'))
        if color:
            line = f'<span style="color:{color}">[{stamp}] {safe}</span>'
        else:
            line = f'[{stamp}] {safe}'
        self.log_view.appendHtml(line)
        # 自动滚到底
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- 输出目录 ----------

    def open_output_dir(self) -> None:
        folder = self.config.output_path
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(str(folder))   # noqa: S606
            else:
                subprocess.Popen(['xdg-open', str(folder)])
        except Exception as e:
            InfoBar.error('打开失败', str(e)[:200], duration=4000,
                          position=InfoBarPosition.TOP, parent=self)
