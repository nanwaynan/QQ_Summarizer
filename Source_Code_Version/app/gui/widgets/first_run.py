"""首次启动引导

三步引导，帮助新用户配好环境：
1. 配置 AI 接口
2. 启动服务（含扫码登录）
3. 选择群聊

设计要点：
- 引导是「非阻塞」的：点「去设置页填写」只隐藏窗口并跳页，不关闭
- 「下一步」只推进步骤，绝不关闭窗口 —— 只有到最后一步才变成「完成」
- 顶部始终显示当前进度（第 N / 3 步），避免用户不知道自己在哪
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon as FIF,
    PrimaryPushButton, PushButton, StrongBodyLabel, SubtitleLabel,
    MessageBoxBase,
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

    # 每一步点「去操作」时跳转的页面
    STEP_PAGES = ['settings', 'manual', 'settings']

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
        self._finished = False

        self._build_ui()
        self._render_step()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.viewLayout.setSpacing(PAGE_SPACING)

        title = SubtitleLabel('欢迎使用 QQ 群聊总结器')
        self.viewLayout.addWidget(title)

        intro = CaptionLabel(
            '按下面 3 步配置好，之后就能一键生成群聊总结了。\n'
            '点「下一步」回到本窗口继续；也可以直接「跳过」先随便看看。')
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
        self.btn_prev = PushButton('上一步')
        self.btn_prev.clicked.connect(self._on_prev)
        self.btn_prev.setVisible(False)
        row.addWidget(self.btn_prev)

        self.btn_action = PushButton('')
        self.btn_action.clicked.connect(self._on_action)
        row.addWidget(self.btn_action)

        self.btn_skip = PushButton('跳过引导')
        self.btn_skip.clicked.connect(self._finish)
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        self.viewLayout.addLayout(row)

        # 底部按钮
        # 注意：MessageBoxBase 内部已经把 yesButton 连到 accept()，
        # 这里必须 disconnect，否则点「下一步」会直接关窗（v1.0.2 的 bug）。
        self.yesButton.setCheckable(False)   # 防误触
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_next)
        self.yesButton.setText('下一步')

        self.cancelButton.setText('稍后再说')
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._on_later)

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
                '该填什么，到「设置」页就能看到。\n'
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
                '完成后点本窗口的「完成」结束引导。')
            self.btn_action.setText('去选择群聊')
            self.btn_action.setIcon(FIF.MENU)

        # 按钮文案 + 可见性
        last = idx >= len(self.STEPS) - 1
        self.yesButton.setText('完成' if last else '下一步')
        self.btn_prev.setVisible(idx > 0)

    # ---------- 交互 ----------

    def _on_action(self) -> None:
        """跳转到对应页面，并临时隐藏本窗口。"""
        target = self.STEP_PAGES[self._current]
        self.goto_page.emit(target)
        # 隐藏而不是关闭；再点「下一步」会自动回到这里
        self._hidden_for_action = True
        self.hide()

    def _on_next(self) -> None:
        if self._current >= len(self.STEPS) - 1:
            self._finish()
            return
        self._current += 1
        self._render_step()
        # 如果刚从设置页回来，确保窗口可见
        if not self.isVisible():
            self.show()

    def _on_later(self) -> None:
        """稍后再说：只关窗，不写 first_run_done —— 下次启动还会出现。"""
        self.logger.info('用户选择稍后配置引导')
        self.reject()

    def _on_prev(self) -> None:
        if self._current > 0:
            self._current -= 1
            self._render_step()

    def _finish(self) -> None:
        # accept() 会触发 finished 信号；防止重复点击导致重复写日志/存盘
        if self._finished:
            return
        self._finished = True

        self.config.set('first_run_done', True)
        try:
            self.config.save()
        except Exception:
            pass
        self.logger.info('首次引导已完成')
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 从设置页返回：重新渲染，让状态与配置一致
        if getattr(self, '_hidden_for_action', False):
            self._hidden_for_action = False
            self._render_step()

    def closeEvent(self, event) -> None:  # noqa: N802
        """点右上角 X 关闭 = 稍后再说，不标记完成。"""
        self.logger.info('引导对话框被关闭（下次启动会再次出现）')
        super().closeEvent(event)
