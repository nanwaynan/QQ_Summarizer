"""使用帮助页

面向新用户，说明环境要求、首次使用步骤、常见问题。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon as FIF,
    HyperlinkButton, PushButton, ScrollArea, StrongBodyLabel, SubtitleLabel,
)

from ...config import Config
from ...constants import (
    CARD_MARGINS, CARD_SPACING, COLOR_ERROR, COLOR_MUTED,
    COLOR_SUCCESS, COLOR_WARN, PAGE_MARGINS, PAGE_SPACING,
)
from ...logger import Logger


class HelpPage(QWidget):
    """使用帮助页。"""

    def __init__(self, config: Config, logger: Logger,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.logger = logger
        self.setObjectName('HelpPage')
        self._build_ui()
        self.refresh()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        # 不设 background: transparent —— 透明视口会透出纯黑调色板
        scroll.setStyleSheet('QScrollArea { border: none; }')

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        root.addWidget(SubtitleLabel('使用帮助'))

        # ---- 环境状态 ----
        root.addWidget(StrongBodyLabel('环境检测'))
        self.env_card = CardWidget()
        el = QVBoxLayout(self.env_card)
        el.setContentsMargins(*CARD_MARGINS)
        el.setSpacing(CARD_SPACING)

        self.env_qq = BodyLabel('QQ 客户端：检测中...')
        self.env_napcat = BodyLabel('NapCat：检测中...')
        self.env_status = CaptionLabel('')
        el.addWidget(self.env_qq)
        el.addWidget(self.env_napcat)
        el.addWidget(self.env_status)

        row = QHBoxLayout()
        btn_refresh = PushButton(FIF.SYNC, '重新检测')
        btn_refresh.clicked.connect(self.refresh)
        row.addWidget(btn_refresh)
        row.addStretch(1)
        el.addLayout(row)
        root.addWidget(self.env_card)

        # ---- 前置要求 ----
        root.addWidget(StrongBodyLabel('使用前需要什么'))

        req = CardWidget()
        rl = QVBoxLayout(req)
        rl.setContentsMargins(*CARD_MARGINS)
        rl.setSpacing(CARD_SPACING)

        rl.addWidget(BodyLabel('① QQ 客户端（必须）'))
        rl.addWidget(CaptionLabel(
            'NapCat 通过注入本机 QQ 客户端读取群消息，所以必须先装 QQ。\n'
            '推荐版本：QQ NT 9.9.25 及以上（官方最新版即可）。'))
        link_qq = HyperlinkButton('https://im.qq.com/pcqq',
                                  '下载 QQ 客户端', self)
        rl.addWidget(link_qq)

        rl.addWidget(BodyLabel('② 一个 AI 接口（必须）'))
        rl.addWidget(CaptionLabel(
            '任意 OpenAI 兼容接口即可。推荐支持「图片识别」的模型，\n'
            '这样图片甄选功能才可用（纯文本模型只能生成文字报告）。'))

        rl.addWidget(BodyLabel('③ 首次需要扫码登录一次'))
        rl.addWidget(CaptionLabel(
            'QQ 的登录凭证保存在系统用户目录里，不在本程序目录中，\n'
            '所以每台新电脑首次使用都必须扫码一次（之后可免扫码）。\n'
            '请准备一个用于读取消息的 QQ 小号。'))
        root.addWidget(req)

        # ---- 使用步骤 ----
        root.addWidget(StrongBodyLabel('首次使用步骤'))

        steps = CardWidget()
        sl = QVBoxLayout(steps)
        sl.setContentsMargins(*CARD_MARGINS)
        sl.setSpacing(CARD_SPACING)

        for i, (title, desc) in enumerate([
            ('启动服务', '点「手动总结」页的「启动服务」，程序会自动拉起 NapCat。\n'
                         '若弹出 UAC 提权窗口，请点「是」。'),
            ('扫码登录', '出现二维码后用手机 QQ 扫码，登录你要用来读消息的账号。'),
            ('配置 AI', '到「设置」页填写接口地址、模型名、API Key，点「测试连接」。'),
            ('选择群聊', '同一页面下方刷新群列表，勾选要总结的群。'),
            ('开始总结', '回到「手动总结」页，选时间段，点「开始总结」。'),
        ], 1):
            sl.addWidget(BodyLabel(f'{i}. {title}'))
            sl.addWidget(CaptionLabel(desc))
        root.addWidget(steps)

        # ---- 自动化 ----
        root.addWidget(StrongBodyLabel('想要每天自动总结'))

        auto_card = CardWidget()
        al = QVBoxLayout(auto_card)
        al.setContentsMargins(*CARD_MARGINS)
        al.setSpacing(CARD_SPACING)
        al.addWidget(CaptionLabel(
            '到「自动任务」页：\n'
            '  · 开启「每日自动总结」，设置触发时间（默认 12:30）\n'
            '  · 开启「开机自启」，程序会随 Windows 启动并最小化到托盘\n'
            '  · 建议同时保持 NapCat 常驻运行，否则离线时段的消息无法回溯'))
        root.addWidget(auto_card)

        # ---- 常见问题 ----
        root.addWidget(StrongBodyLabel('常见问题'))

        faq = CardWidget()
        fl = QVBoxLayout(faq)
        fl.setContentsMargins(*CARD_MARGINS)
        fl.setSpacing(CARD_SPACING)

        faqs = [
            ('启动服务没反应 / 一直等待',
             '1. 看有没有弹出 UAC 提权窗口，需要点「是」\n'
             '2. 确认 QQ 已安装（见上方环境检测）\n'
             '3. 任务管理器结束所有 QQ.exe，再重试'),
            ('提示「账号存在安全风险」',
             '腾讯的风控拦截。用手机 QQ 登录该账号，按提示解除限制，\n'
             '隔几小时后再试。频繁切换登录容易触发。'),
            ('数据不完整警告',
             'NapCat 未全程在线，它只能拉到自己在运行时收到的消息。\n'
             '建议保持 NapCat 常驻运行。'),
            ('图片很少或没有',
             '可能当天图少，或图片都没达到最低分门槛。\n'
             '到「设置」页把「图片最低分」从 4 调到 3 试试。'),
            ('报告只有文字没有图',
             'AI 接口不支持图片输入（非多模态）。\n'
             '换成支持 vision 的模型，或接受纯文字报告。'),
            ('QQ 版本不匹配',
             'NapCat 的版本支持表会滞后于 QQ 更新。\n'
             '如果 QQ 刚自动升级，可能需要等 NapCat 出新版，\n'
             '或把 QQ 回退到支持列表内的版本。'),
        ]
        for q, a in faqs:
            fl.addWidget(BodyLabel(f'Q：{q}'))
            fl.addWidget(CaptionLabel(f'A：{a}'))
            fl.addSpacing(4)
        root.addWidget(faq)

        root.addStretch(1)
        scroll.setWidget(container)
        outer.addWidget(scroll)

    # ---------- 环境检测 ----------

    def refresh(self) -> None:
        """刷新环境状态显示。"""
        from ...core.qqversion import detect_qq, napcat_version

        # QQ
        info = detect_qq()
        if info['installed']:
            mark = '✓' if info['compatible'] else '⚠'
            color = COLOR_SUCCESS if info['compatible'] else COLOR_WARN
            self.env_qq.setText(f'{mark} QQ 客户端：{info["version"] or "未知版本"}')
            self.env_qq.setTextColor(*color)
        else:
            self.env_qq.setText('✗ QQ 客户端：未检测到')
            self.env_qq.setTextColor(*COLOR_ERROR)

        # NapCat
        nv = napcat_version()
        nd = str(self.config.get('napcat_dir') or '')
        if nv:
            self.env_napcat.setText(f'✓ NapCat：v{nv}')
            self.env_napcat.setTextColor(*COLOR_SUCCESS)
        elif nd:
            from pathlib import Path
            ok = (Path(nd) / 'napcat.mjs').exists()
            if ok:
                self.env_napcat.setText(f'✓ NapCat：已就绪（{nd}）')
                self.env_napcat.setTextColor(*COLOR_SUCCESS)
            else:
                self.env_napcat.setText(f'✗ NapCat：目录异常（{nd}）')
                self.env_napcat.setTextColor(*COLOR_ERROR)
        else:
            self.env_napcat.setText('✗ NapCat：未找到')
            self.env_napcat.setTextColor(*COLOR_ERROR)

        # 汇总提示
        if info['installed'] and info['compatible'] and (nv or nd):
            self.env_status.setText('环境就绪，可以开始使用')
            self.env_status.setTextColor(*COLOR_SUCCESS)
        else:
            self.env_status.setText(info['message'].replace('\n', ' '))
            self.env_status.setTextColor(*COLOR_MUTED)
