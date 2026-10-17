"""自动任务页

- 每日自动总结开关 + 触发时间
- 开机自启开关
- 错过补跑
- 下次执行时间显示
- 立即执行一次
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, ComboBox, FluentIcon as FIF,
    InfoBar, InfoBarPosition, PrimaryPushButton, PushButton, StrongBodyLabel,
    SubtitleLabel, SwitchButton, TimePicker, setFont,
)
from PySide6.QtCore import QTime

from ...config import Config
from ...logger import Logger


class AutoPage(QWidget):
    """自动任务页。"""

    def __init__(self, config: Config, logger: Logger,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.logger = logger
        self.setObjectName('AutoPage')

        self._build_ui()
        self._load()
        self._start_timer()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        root.addWidget(SubtitleLabel('自动任务'))

        # ---- 每日总结 ----
        root.addWidget(StrongBodyLabel('每日自动总结'))

        card1 = CardWidget()
        l1 = QVBoxLayout(card1)
        l1.setContentsMargins(20, 16, 20, 16)
        l1.setSpacing(12)

        row_on = QHBoxLayout()
        row_on.addWidget(BodyLabel('启用每日自动总结'))
        row_on.addStretch(1)
        self.sw_auto = SwitchButton()
        self.sw_auto.setOnText('已开启')
        self.sw_auto.setOffText('已关闭')
        self.sw_auto.checkedChanged.connect(self._on_auto_toggled)
        row_on.addWidget(self.sw_auto)
        l1.addLayout(row_on)

        row_time = QHBoxLayout()
        row_time.addWidget(BodyLabel('每天触发时间'))
        row_time.addStretch(1)
        self.tp_time = TimePicker()
        self.tp_time.timeChanged.connect(self._on_time_changed)
        row_time.addWidget(self.tp_time)
        l1.addLayout(row_time)

        self.auto_hint = CaptionLabel(
            '总结范围：昨天 12:00 ~ 今天 12:00（与归档口径一致）。\n'
            '若触发时间早于 12:00，则总结「前天 12:00 ~ 昨天 12:00」。')
        l1.addWidget(self.auto_hint)

        row_catch = QHBoxLayout()
        row_catch.addWidget(BodyLabel('错过时补跑'))
        self.catch_hint = CaptionLabel('（开机后若发现今天还没跑，则立即补一次）')
        row_catch.addWidget(self.catch_hint)
        row_catch.addStretch(1)
        self.sw_catch = SwitchButton()
        self.sw_catch.setOnText('开启')
        self.sw_catch.setOffText('关闭')
        self.sw_catch.checkedChanged.connect(self._on_catch_toggled)
        row_catch.addWidget(self.sw_catch)
        l1.addLayout(row_catch)

        root.addWidget(card1)

        # ---- 状态 ----
        card2 = CardWidget()
        l2 = QVBoxLayout(card2)
        l2.setContentsMargins(20, 16, 20, 16)
        l2.setSpacing(8)

        self.next_label = BodyLabel('下次执行：—')
        setFont(self.next_label, 14)
        l2.addWidget(self.next_label)

        self.history_label = CaptionLabel('')
        l2.addWidget(self.history_label)

        row_btn = QHBoxLayout()
        self.btn_run_now = PushButton('立即执行一次')
        self.btn_run_now.clicked.connect(self.run_now)
        row_btn.addWidget(self.btn_run_now)
        row_btn.addStretch(1)
        l2.addLayout(row_btn)

        root.addWidget(card2)

        # ---- 开机自启 ----
        root.addWidget(StrongBodyLabel('开机自启'))

        card3 = CardWidget()
        l3 = QVBoxLayout(card3)
        l3.setContentsMargins(20, 16, 20, 16)
        l3.setSpacing(8)

        row_boot = QHBoxLayout()
        row_boot.addWidget(BodyLabel('随 Windows 启动'))
        row_boot.addStretch(1)
        self.sw_boot = SwitchButton()
        self.sw_boot.setOnText('已开启')
        self.sw_boot.setOffText('已关闭')
        self.sw_boot.checkedChanged.connect(self._on_boot_toggled)
        row_boot.addWidget(self.sw_boot)
        l3.addLayout(row_boot)

        self.boot_hint = CaptionLabel(
            '开启后程序会在登录 Windows 时自动启动并最小化到托盘。\n'
            '注意：需要 NapCat 服务也在运行，否则无法拉取消息。')
        l3.addWidget(self.boot_hint)

        root.addWidget(card3)
        root.addStretch(1)

    # ---------- 加载 ----------

    def _load(self) -> None:
        c = self.config
        self.sw_auto.setChecked(bool(c.get('auto_enabled', False)))
        self.sw_catch.setChecked(bool(c.get('auto_catch_up', False)))
        self.sw_boot.setChecked(bool(c.get('autostart', False)))

        t = str(c.get('auto_time', '12:30'))
        try:
            hh, mm = t.split(':')
            self.tp_time.setTime(QTime(int(hh), int(mm)))
        except Exception:
            self.tp_time.setTime(QTime(12, 30))

        self._update_next_label()
        self._update_history_label()

    # ---------- 事件 ----------

    def _on_auto_toggled(self, checked: bool) -> None:
        self.config.set('auto_enabled', bool(checked))
        self._persist()
        self._update_next_label()
        try:
            from ...scheduler import Scheduler
            sched = Scheduler.instance()
            if sched is not None:
                sched.reschedule()
        except Exception as e:
            self.logger.warn(f'调度器刷新失败: {e}')

    def _on_catch_toggled(self, checked: bool) -> None:
        self.config.set('auto_catch_up', bool(checked))
        self._persist()

    def _on_time_changed(self, t: QTime) -> None:
        self.config.set('auto_time', f'{t.hour():02d}:{t.minute():02d}')
        self._persist()
        self._update_next_label()
        try:
            from ...scheduler import Scheduler
            sched = Scheduler.instance()
            if sched is not None:
                sched.reschedule()
        except Exception:
            pass

    def _on_boot_toggled(self, checked: bool) -> None:
        try:
            from ...autostart import set_autostart, is_autostart_enabled
            ok = set_autostart(bool(checked))
            if ok:
                self.config.set('autostart', bool(checked))
                self._persist()
                actual = is_autostart_enabled()
                if actual != bool(checked):
                    self.logger.warn(
                        f'注册表状态与预期不符（期望 {checked}，实际 {actual}）')
                    self.sw_boot.setChecked(actual)
            else:
                self.sw_boot.setChecked(is_autostart_enabled())
                InfoBar.warning(
                    '设置失败', '无法写入开机自启项（可能被系统策略限制）',
                    duration=5000, position=InfoBarPosition.TOP_RIGHT,
                    parent=self)
        except Exception as e:
            InfoBar.error('设置失败', str(e)[:200], duration=5000,
                          position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _persist(self) -> None:
        try:
            self.config.save()
        except Exception as e:
            self.logger.warn(f'配置保存失败: {e}')

    # ---------- 状态显示 ----------

    def _update_next_label(self) -> None:
        if not self.config.get('auto_enabled', False):
            self.next_label.setText('下次执行：未启用')
            return
        nxt = compute_next_run(str(self.config.get('auto_time', '12:30')))
        if nxt is None:
            self.next_label.setText('下次执行：时间格式错误')
            return
        delta = nxt - dt.datetime.now()
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        self.next_label.setText(
            f'下次执行：{nxt:%Y-%m-%d %H:%M}（还有 {h} 小时 {m} 分）')

    def _update_history_label(self) -> None:
        st = self.config.get('auto_state') or {}
        if not isinstance(st, dict):
            st = {}
        last = st.get('last_run') or '从未执行'
        last_date = st.get('last_date') or '—'
        self.history_label.setText(
            f'上次执行：{last}    已完成的周期：{last_date}')

    def refresh_status(self) -> None:
        """页面切换过来时调用。"""
        self._load()

    def _start_timer(self) -> None:
        """每分钟刷新倒计时。"""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_next_label)
        self._timer.start(60_000)

    # ---------- 立即执行 ----------

    def run_now(self) -> None:
        """立即执行一次自动总结（用自动任务的参数）。"""
        from qfluentwidgets import MessageBox
        box = MessageBox(
            '立即执行',
            '将按自动任务的设置（总结「昨天12:00~今天12:00」）执行一次总结。\n'
            '执行过程中可以切到「手动总结」页看进度。\n\n是否继续？',
            self.window())
        if not box.exec():
            return

        try:
            from ...scheduler import Scheduler
            sched = Scheduler.instance()
            if sched is None:
                InfoBar.warning(
                    '调度器未就绪', '请重启程序后重试',
                    duration=4000, position=InfoBarPosition.TOP_RIGHT,
                    parent=self)
                return
            ok, msg = sched.run_now()
            if ok:
                InfoBar.success('已启动', msg, duration=4000,
                                position=InfoBarPosition.TOP_RIGHT, parent=self)
                # 跳到手动页看进度
                w = self.window()
                if hasattr(w, 'switch_to'):
                    w.switch_to('manual')
            else:
                InfoBar.warning('无法执行', msg, duration=5000,
                                position=InfoBarPosition.TOP_RIGHT, parent=self)
        except Exception as e:
            InfoBar.error('执行失败', str(e)[:200], duration=5000,
                          position=InfoBarPosition.TOP_RIGHT, parent=self)


def compute_next_run(auto_time: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """计算下一次执行时间。"""
    now = now or dt.datetime.now()
    try:
        hh, mm = auto_time.split(':')
        h, m = int(hh), int(mm)
    except Exception:
        return None
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target
