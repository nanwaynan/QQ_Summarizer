"""首次启动引导

三步引导，帮助新用户配好环境：
1. 配置 AI 接口
2. 启动服务（含扫码登录）
3. 选择群聊

完成后写入 first_run_done 标记，不再弹出。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon as FIF,
    IndeterminateProgressBar, InfoBar, InfoBarPosition, MessageBoxBase,
    PrimaryPushButton, PushButton, StrongBodyLabel, SubtitleLabel,
)

from ...config import Config
from ...constants import (
    CARD_MARGINS, CARD_SPACING, COLOR_MUTED, COLOR_SUCCESS, COLOR_WARN,
    PAGE_SPACING,
)
from ...logger import Logger


class FirstRunDialog(MessageBoxBase):
    """首次启动引导对话框。

    Signals:
        goto_page(str) : 请求主窗口跳转到某页
    """

    goto_page = Signal(str)

    STEPS = [
        ('配置 AI 接口', '让程序知道用哪个大模型来总结'),
        ('启动服务并登录 QQ', 'NapCat 需要通过你的 QQ 账号读取群消息'),
        ('选择要总结的群', '告诉程序关注哪个群'),
    ]

    def __init__(self, config: Config, logger: Logger,
                 parent: QWidget | None = None):
        if parent is None:
            from PySide6.QtWidgets import QApplication
            parent = QApplication.activeWindow()
        super().__init__(parent)
        self.config = config
        self.logger = logger
        self._current = 0
        self._hidden_for_action = False

        self._build_ui()
        self._render_step()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.viewLayout.setSpacing(PAGE_SPACING)

        title = SubtitleLabel('欢迎使用 QQ 群聊总结器')
        self.viewLayout.addWidget(title)

        intro = CaptionLabel(
            '按下面 3 步配置好，之后就能一键生成群聊总结了。\n'
            '每一步完成后点「下一步」继续，也可以随时点「跳过」。')
        intro.setWordWrap(True)
        self.viewLayout.addWidget(intro)

        # 步骤指示条
        self.step_labels: list[BodyLabel] = []
        for i, (name, _) in enumerate(self.STEPS, 1):
            lab = BodyLabel(f'{i}. {name}')
            self.step_labels.append(lab)
            self.viewLayout.addWidget(lab)

        # 当前步骤说明卡片
        self.card = CardWidget()
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(*CARD_MARGINS)
        cl.setSpacing(CARD_SPACING)
        self.card_title = StrongBodyLabel('')
        self.card_desc = CaptionLabel('')
        self.card_desc.setWordWrap(True)
        self.card_hint = CaptionLabel('')
        self.card_hint.setWordWrap(True)
        cl.addWidget(self.card_title)
        cl.addWidget(self.card_desc)
        cl.addWidget(self.card_hint)
        self.viewLayout.addWidget(self.card)

        # 操作区
        row = QHBoxLayout()
        self.btn_action = PushButton('')
        self.btn_action.clicked.connect(self._on_action)
        row.addWidget(self.btn_action)

        self.btn_skip = PushButton('跳过引导')
        self.btn_skip.clicked.connect(self._on_skip)
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        self.viewLayout.addLayout(row)

        # 底部按钮
        self.yesButton.setText('下一步')
        self.yesButton.clicked.connect(self._on_next)
        self.cancelButton.setText('关闭')

    # ---------- 渲染 ----------

    def _render_step(self) -> None:
        idx = self._current
        name, purpose = self.STEPS[idx]

        # 步骤条高亮
        for i, lab in enumerate(self.step_labels):
            if i < idx:
                lab.setTextColor(*COLOR_SUCCESS)
                lab.setText(f'✓ {i+1}. {self.STEPS[i][0]}')
            elif i == idx:
                lab.setTextColor(*COLOR_WARN)
                lab.setText(f'▶ {i+1}. {self.STEPS[i][0]}')
            else:
                lab.setTextColor(*COLOR_MUTED)
                lab.setText(f'{i+1}. {self.STEPS[i][0]}')

        self.card_title.setText(f'第 {idx+1} 步：{name}')
        self.card_desc.setText(purpose)

        if idx == 0:
            self.card_hint.setText(
                '该填写什么，到「设置」页就能看到。\n'
                '填完点「测试连接」，显示成功即完成本步。')
            self.btn_action.setText('去设置页填写')
            self.btn_action.setIcon(FIF.SETTING)
        elif idx == 1:
            self.card_hint.setText(
                '点「启动服务」后程序会自动拉起 NapCat。\n'
                '若弹出 UAC 提权窗口请点「是」；随后用手机 QQ 扫码登录。')
            self.btn_action.setText('去启动服务')
            self.btn_action.setIcon(FIF.POWER_BUTTON)
        else:
            self.card_hint.setText(
                '到「设置」页刷新群列表，勾选要总结的群，然后保存。\n'
                '保存后点「完成」结束引导。')
            self.btn_action.setText('去选择群聊')
            self.btn_action.setIcon(FIF.MENU)

        # 最后一步的按钮文案
        self.yesButton.setText('完成' if idx == len(self.STEPS) - 1 else '下一步')

    # ---------- 交互 ----------

    def _on_action(self) -> None:
        """跳转到对应设置页。"""
        target = ['settings', 'manual', 'settings'][self._current]
        self.goto_page.emit(target)
        # 设置页需要滚动到对应区域，这里只保证页面切换
        self.hide()   # 暂时隐藏，方便用户操作
        self._hidden_for_action = True

    def _on_next(self) -> None:
        if self._current >= len(self.STEPS) - 1:
            self._finish()
            return
        self._current += 1
        self._render_step()

    def _on_skip(self) -> None:
        self._finish()

    def _finish(self) -> None:
        self.config.set('first_run_done', True)
        try:
            self.config.save()
        except Exception:
            pass
        self.logger.info('首次引导已完成')
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 若之前因跳转隐藏过，重新显示时回到当前步骤
        if getattr(self, '_hidden_for_action', False):
            self._hidden_for_action = False
            self._render_step()
