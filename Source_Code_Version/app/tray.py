"""系统托盘

用 Qt 原生 QSystemTrayIcon（不额外装 pystray）。
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from qfluentwidgets import FluentIcon as FIF

from .config import Config
from .logger import Logger


class TrayIcon(QObject):
    """托盘图标 + 右键菜单。"""

    def __init__(self, window, config: Config, logger: Logger):
        super().__init__()
        self.window = window
        self.config = config
        self.logger = logger

        self._build()
        window.tray_icon = self  # 供窗口 closeEvent 使用

    # ---------- 构建 ----------

    def _build(self) -> None:
        icon = self._app_icon()
        self.tray = QSystemTrayIcon(icon, self.window)
        self.tray.setToolTip('QQ 群聊总结器')

        menu = QMenu()

        act_show = QAction('显示主窗口', menu)
        act_show.triggered.connect(self._show_window)
        menu.addAction(act_show)

        menu.addSeparator()

        act_summary = QAction('立即总结', menu)
        act_summary.triggered.connect(self._run_summary)
        menu.addAction(act_summary)

        act_open = QAction('打开输出目录', menu)
        act_open.triggered.connect(self._open_output)
        menu.addAction(act_open)

        menu.addSeparator()

        act_auto = QAction('自动任务状态', menu)
        act_auto.setEnabled(False)
        self._act_auto = act_auto
        menu.addAction(act_auto)

        menu.addSeparator()

        act_quit = QAction('退出', menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        # 定时刷新"自动任务状态"菜单项
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_menu)
        self._timer.start(60_000)
        self._refresh_menu()

    def _app_icon(self) -> QIcon:
        """托盘图标（优先用应用图标，缺失时回退内置图标）。"""
        from .resources import load_icon
        ico = load_icon()
        if ico is not None:
            return ico
        try:
            return FIF.CHAT.icon()
        except Exception:
            return QIcon()

    # ---------- 事件 ----------

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _run_summary(self) -> None:
        try:
            from .scheduler import Scheduler
            sched = Scheduler.instance()
            if sched is None:
                self.notify('无法执行', '调度器未就绪')
                return
            ok, msg = sched.run_now()
            self.notify('立即总结' if ok else '无法执行', msg)
            if ok:
                self._show_window()
                if hasattr(self.window, 'switch_to'):
                    self.window.switch_to('manual')
        except Exception as e:
            self.notify('执行失败', str(e)[:100])

    def _open_output(self) -> None:
        try:
            self.window.switch_to('manual')
            page = self.window._pages.get('manual')
            if page is not None and hasattr(page, 'open_output_dir'):
                page.open_output_dir()
        except Exception:
            pass

    def _quit(self) -> None:
        try:
            self.window.quit_app()
        except Exception:
            QApplication.quit()

    def _refresh_menu(self) -> None:
        try:
            enabled = bool(self.config.get('auto_enabled', False))
            if not enabled:
                self._act_auto.setText('自动任务：未启用')
                return
            t = str(self.config.get('auto_time', '12:30'))
            from .scheduler import Scheduler
            sched = Scheduler.instance()
            nxt = sched.next_run_time() if sched else None
            if nxt:
                self._act_auto.setText(f'下次执行：{nxt:%m-%d %H:%M}')
            else:
                self._act_auto.setText(f'自动任务：每天 {t}')
        except Exception:
            pass

    # ---------- 通知 ----------

    def notify(self, title: str, message: str, msecs: int = 4000) -> None:
        """弹托盘气泡通知。"""
        try:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information,
                                  msecs)
        except Exception:
            pass
