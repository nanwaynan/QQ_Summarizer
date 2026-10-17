"""日志页

实时显示日志（内存缓冲 + 订阅），支持级别筛选和导出。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, ComboBox, InfoBar, InfoBarPosition,
    PlainTextEdit, PushButton, SubtitleLabel, FluentIcon as FIF,
    isDarkTheme,
)

from ...config import Config
from ...constants import COLOR_ERROR, COLOR_WARN, level_color
from ...logger import Logger, LogRecord


def _mono_font():
    """等宽字体（日志显示用）。"""
    from PySide6.QtGui import QFont
    f = QFont('Cascadia Mono', 10)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


class LogsPage(QWidget):
    """日志页。"""

    def __init__(self, config: Config, logger: Logger,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.logger = logger
        self.setObjectName('LogsPage')

        self._min_level = 'DEBUG'
        self._build_ui()

        # 订阅实时日志
        self._sink = lambda rec: self._queue.append(rec)
        self._queue: list[LogRecord] = []
        self.logger.add_sink(self._sink)

        # 定时批量刷新（避免频繁重绘）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._flush)
        self._timer.start(400)

        self._load_history()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        root.addWidget(SubtitleLabel('运行日志'))

        bar = QHBoxLayout()
        bar.addWidget(CaptionLabel('级别'))
        self.cmb_level = ComboBox()
        self.cmb_level.addItems(['全部', 'INFO 以上', 'WARN 以上', '仅 ERROR'])
        self.cmb_level.setCurrentIndex(0)
        self.cmb_level.currentIndexChanged.connect(self._on_level_changed)
        bar.addWidget(self.cmb_level)

        bar.addStretch(1)

        btn_open = PushButton(FIF.DOCUMENT, '打开日志文件')
        btn_open.clicked.connect(self._open_log_file)
        bar.addWidget(btn_open)

        btn_export = PushButton(FIF.SAVE, '导出')
        btn_export.clicked.connect(self._export)
        bar.addWidget(btn_export)

        btn_clear = PushButton(FIF.DELETE, '清空显示')
        btn_clear.clicked.connect(self._clear_view)
        bar.addWidget(btn_clear)

        root.addLayout(bar)

        # 用 qfluentwidgets 的 PlainTextEdit（自动适配主题）
        self.view = PlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(_mono_font())
        root.addWidget(self.view, 1)

    # ---------- 日志 ----------

    def _load_history(self) -> None:
        recs = self.logger.recent(500, self._min_level)
        for r in recs:
            self._append(r)

    def _on_level_changed(self, idx: int) -> None:
        self._min_level = ['DEBUG', 'INFO', 'WARN', 'ERROR'][max(0, min(3, idx))]
        self.view.clear()
        self._load_history()

    def _flush(self) -> None:
        """把队列里的日志写入视图。"""
        if not self._queue:
            return
        from ...logger import LEVELS
        try:
            min_idx = LEVELS.index(self._min_level)
        except ValueError:
            min_idx = 0

        batch, self._queue = self._queue, []
        for rec in batch:
            try:
                if LEVELS.index(rec.level) >= min_idx:
                    self._append(rec)
            except ValueError:
                continue

    def _append(self, rec: LogRecord) -> None:
        color = level_color(rec.level, isDarkTheme())
        safe = (rec.format().replace('&', '&amp;')
                .replace('<', '&lt;').replace('>', '&gt;'))
        if color:
            self.view.appendHtml(f'<span style="color:{color}">{safe}</span>')
        else:
            self.view.appendPlainText(safe)
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_view(self) -> None:
        self.view.clear()
        self._queue.clear()

    def _open_log_file(self) -> None:
        import os
        import subprocess
        import sys
        f = Path(self.logger._log_file) if self.logger._log_file else None
        if f is None or not f.exists():
            InfoBar.info('暂无日志文件', '日志文件尚未创建',
                         duration=3000, position=InfoBarPosition.TOP_RIGHT,
                         parent=self)
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(f))   # noqa: S606
            else:
                subprocess.Popen(['xdg-open', str(f)])
        except Exception as e:
            InfoBar.error('打开失败', str(e)[:150], duration=4000,
                          position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _export(self) -> None:
        default = f'qsummarizer-log-{dt.datetime.now():%Y%m%d-%H%M%S}.txt'
        path, _ = QFileDialog.getSaveFileName(self, '导出日志', default,
                                              'Text (*.txt)')
        if not path:
            return
        try:
            n = self.logger.export(Path(path), self._min_level)
            InfoBar.success('导出成功', f'已导出 {n} 条日志',
                            duration=3000, position=InfoBarPosition.TOP_RIGHT,
                            parent=self)
        except Exception as e:
            InfoBar.error('导出失败', str(e)[:150], duration=4000,
                          position=InfoBarPosition.TOP_RIGHT, parent=self)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.logger.remove_sink(self._sink)
        except Exception:
            pass
        event.accept()
