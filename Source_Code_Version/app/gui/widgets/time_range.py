"""时间段选择控件

需求要点：
- 起止时间精确到「日期 + 小时」，最小单位 1 小时
- 跨度不超过 24 小时
- 起点必须早于终点，不能选未来
- 实时显示摘要，不合格时禁用开始按钮

注意：qfluentwidgets 的 TimePicker 精度到分钟，这里在读取时把分钟归零，
保证对外提供的时间点都是整小时，符合需求。

布局说明（起止各占一行）：
  DatePicker 在英文月份下会把月份列撑到 ~177px（"September"），整个日期控件
  可达 337px，与 240px 的时间控件放在同一行共需 ~1232px；而卡片可用宽度只有
  ~984px（窗口缩到最小 900 时仅 804px）。QHBoxLayout 空间不足时不会压缩子控件，
  而是让它们几何重叠，导致日期末位与小时数字叠印（"15"+"12" 看着像 "152"）。
  因此这里：① 用中文日期控件（三列各 80px，共 240px）；② 起止各占一行，
  单行只需约 534px，在最小窗口下仍有充足余量。
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QDate, QTime, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, PushButton, TimePicker, ZhDatePicker,
)

MAX_SPAN_HOURS = 24


class TimeRangePicker(CardWidget):
    """时间段选择器。

    Signals:
        validChanged(bool) : 校验结果变化（或值变化但仍有效）
    """

    validChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._last_valid = True
        self._build_ui()
        self._apply_defaults()
        self._validate()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        title = BodyLabel('选择时间段')
        title.setStyleSheet('font-weight: 600;')
        root.addWidget(title)

        # 起点行 / 终点行（各占一行，避免与英文月份日期控件挤压重叠）
        self.start_date, self.start_time = self._add_row(root, '从')
        self.end_date, self.end_time = self._add_row(root, '到')

        # 快捷按钮
        quick = QHBoxLayout()
        quick.setSpacing(8)
        quick.addWidget(CaptionLabel('快捷'))
        for label, hours in (('最近 1 小时', 1), ('最近 6 小时', 6),
                             ('最近 12 小时', 12), ('最近 24 小时', 24)):
            btn = PushButton(label)
            btn.clicked.connect(lambda _=False, h=hours: self.set_recent(h))
            quick.addWidget(btn)
        btn_y = PushButton('昨天12点起')
        btn_y.clicked.connect(self.set_yesterday_period)
        quick.addWidget(btn_y)
        quick.addStretch(1)
        root.addLayout(quick)

        self.summary = BodyLabel('')
        root.addWidget(self.summary)

        self.hint = CaptionLabel('')
        self.hint.setTextColor('#c42b1c', '#ff99a4')
        root.addWidget(self.hint)

    def _add_row(self, parent: QVBoxLayout, caption: str
                 ) -> tuple[ZhDatePicker, TimePicker]:
        """添加一行「标签 + 日期 + 时间」，返回 (日期控件, 时间控件)。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(CaptionLabel(caption))

        date = ZhDatePicker()
        # 中文格式（年/月/日 各一列），比英文月份窄且无需额外的宽度兜底
        for col in range(3):
            try:
                date.setColumnWidth(col, 80)
            except Exception:
                pass
        date.dateChanged.connect(self._on_changed)
        date.setMinimumWidth(240)
        row.addWidget(date)

        tm = TimePicker()
        tm.setSecondVisible(False)   # 精度到分钟
        tm.timeChanged.connect(self._on_changed)
        tm.setMinimumWidth(240)
        row.addWidget(tm)

        row.addStretch(1)
        parent.addLayout(row)
        return date, tm

    def _apply_defaults(self) -> None:
        self.set_recent(24, emit=False)

    # ---------- 取值/设值 ----------

    def values(self) -> tuple[dt.datetime, dt.datetime]:
        """返回 (start, end)，分钟强制归零（需求：精度到小时）。"""
        sd = self.start_date.date
        st = self.start_time.time
        ed = self.end_date.date
        et = self.end_time.time
        start = dt.datetime(sd.year(), sd.month(), sd.day(), st.hour(), 0)
        end = dt.datetime(ed.year(), ed.month(), ed.day(), et.hour(), 0)
        return start, end

    def set_range(self, start: dt.datetime, end: dt.datetime,
                  emit: bool = True) -> None:
        self._block(True)
        try:
            self.start_date.setDate(QDate(start.year, start.month, start.day))
            self.start_time.setTime(QTime(start.hour, 0))
            self.end_date.setDate(QDate(end.year, end.month, end.day))
            self.end_time.setTime(QTime(end.hour, 0))
        finally:
            self._block(False)
        self._validate(emit=emit)

    def set_recent(self, hours: int, emit: bool = True) -> None:
        """最近 N 小时（结束对齐到当前整点）。"""
        now = dt.datetime.now()
        end = now.replace(minute=0, second=0, microsecond=0)
        start = end - dt.timedelta(hours=min(hours, MAX_SPAN_HOURS))
        self.set_range(start, end, emit=emit)

    def set_yesterday_period(self, emit: bool = True) -> None:
        """昨天 12:00 ~ 今天 12:00（与归档口径一致）。"""
        today = dt.date.today()
        start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(12, 0))
        end = dt.datetime.combine(today, dt.time(12, 0))
        now_hour = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
        if end > now_hour:
            end = now_hour
        self.set_range(start, end, emit=emit)

    def is_valid(self) -> bool:
        return self._last_valid

    def error_text(self) -> str:
        return self.hint.text()

    # ---------- 内部 ----------

    def _block(self, flag: bool) -> None:
        for w in (self.start_date, self.start_time,
                  self.end_date, self.end_time):
            w.blockSignals(flag)

    def _on_changed(self, *_: object) -> None:
        self._validate()

    def _validate(self, emit: bool = True) -> None:
        try:
            start, end = self.values()
        except Exception as e:
            self.summary.setText('')
            self.hint.setText(f'⚠ 时间读取失败：{e}')
            self._set_valid(False, emit)
            return

        now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
        span = (end - start).total_seconds() / 3600

        err = ''
        if end <= start:
            err = '结束时间必须晚于开始时间'
        elif span > MAX_SPAN_HOURS:
            err = f'时间跨度为 {span:.0f} 小时，超过 {MAX_SPAN_HOURS} 小时上限'
        elif end > now:
            err = f'结束时间不能是未来（当前 {now:%Y-%m-%d %H:%M}）'

        if err:
            self.summary.setText(
                f'将总结：{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}')
            self.hint.setText(f'⚠ {err}')
            self._set_valid(False, emit)
        else:
            hours = max(0.0, span)
            h_txt = (f'{int(hours)} 小时' if abs(hours - int(hours)) < 1e-6
                     else f'{hours:.1f} 小时')
            self.summary.setText(
                f'将总结：{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}'
                f'，共 {h_txt}')
            self.hint.setText('')
            self._set_valid(True, emit)

    def _set_valid(self, ok: bool, emit: bool) -> None:
        changed = (ok != self._last_valid)
        self._last_valid = ok
        if emit and (changed or ok):
            self.validChanged.emit(ok)
