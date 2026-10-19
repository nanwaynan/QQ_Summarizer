"""主窗口

左侧导航 + 右侧内容区。页面按需初始化（懒加载），加快启动。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF, NavigationItemPosition, NavigationInterface,
    SubtitleLabel,
)

from ..config import Config
from ..logger import Logger


class MainWindow(QWidget):
    """应用主窗口。"""

    def __init__(self, config: Config, logger: Logger):
        super().__init__()
        self.config = config
        self.logger = logger

        self.setWindowTitle('QQ 群聊总结器')
        self.resize(1120, 760)
        self.setMinimumSize(900, 600)
        # 窗口图标（与 exe/托盘统一）
        try:
            from ..resources import load_icon
            ico = load_icon()
            if ico is not None:
                self.setWindowIcon(ico)
        except Exception:
            pass

        self._pages: dict[str, QWidget] = {}
        self._build_ui()
        self._apply_theme()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 导航
        self.nav = NavigationInterface(self, showMenuButton=True)
        self.nav.setExpandWidth(200)
        self.nav.setMinimumWidth(200)
        root.addWidget(self.nav)

        # 内容区
        self.stack = QStackedWidget(self)
        self.stack.setObjectName('contentStack')
        root.addWidget(self.stack, 1)

        # ---- 页面（懒加载：首次点击才创建）----
        self._nav_specs = [
            ('manual', '手动总结', FIF.HOME, True),
            ('auto', '自动任务', FIF.SYNC, False),
            ('settings', '设置', FIF.SETTING, False),
            ('logs', '日志', FIF.DOCUMENT, False),
        ]

        for key, title, icon, is_default in self._nav_specs:
            self.nav.addItem(
                routeKey=key,
                icon=icon,
                text=title,
                onClick=lambda checked=False, k=key: self.switch_to(k),
                position=NavigationItemPosition.TOP,
                selectable=True,
            )

        # 底部：帮助 / 关于
        self.nav.addItem(
            routeKey='help',
            icon=FIF.HELP,
            text='使用帮助',
            onClick=lambda checked=False: self.switch_to('help'),
            position=NavigationItemPosition.BOTTOM,
            selectable=True,
        )
        self.nav.addItem(
            routeKey='about',
            icon=FIF.INFO,
            text='关于',
            onClick=lambda checked=False: self.switch_to('about'),
            position=NavigationItemPosition.BOTTOM,
            selectable=True,
        )

        # 先加载首页
        self.switch_to('manual')

    def _get_page(self, key: str) -> QWidget | None:
        """懒加载页面。"""
        if key in self._pages:
            return self._pages[key]

        page: QWidget | None = None
        try:
            if key == 'manual':
                from .pages.manual import ManualPage
                page = ManualPage(self.config, self.logger)
            elif key == 'auto':
                from .pages.auto import AutoPage
                page = AutoPage(self.config, self.logger)
            elif key == 'settings':
                from .pages.settings import SettingsPage
                page = SettingsPage(self.config, self.logger)
            elif key == 'logs':
                from .pages.logs import LogsPage
                page = LogsPage(self.config, self.logger)
            elif key == 'help':
                from .pages.help import HelpPage
                page = HelpPage(self.config, self.logger)
            elif key == 'about':
                page = self._build_about()
        except Exception as e:
            self.logger.error(f'加载页面 {key} 失败: {type(e).__name__}: {e}')
            from qfluentwidgets import BodyLabel
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.addWidget(SubtitleLabel(f'页面加载失败：{key}'))
            lay.addWidget(BodyLabel(f'{type(e).__name__}: {e}'))
            lay.addStretch(1)

        if page is not None:
            page.setObjectName(f'page_{key}')
            self.stack.addWidget(page)
            self._pages[key] = page
        return page

    def switch_to(self, key: str) -> None:
        # 离开设置页前，如果存在未保存的修改，先提示
        if not self._confirm_leave_settings(key):
            # 用户选择"留下"：把导航选中态拨回设置页
            try:
                self.nav.setCurrentItem('settings')
            except Exception:
                pass
            return

        page = self._get_page(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        # 同步导航选中态
        try:
            self.nav.setCurrentItem(key)
        except Exception:
            pass
        # 切到自动任务页时刷新下次执行时间
        if key == 'auto' and hasattr(page, 'refresh_status'):
            try:
                page.refresh_status()
            except Exception:
                pass
        if key == 'settings' and hasattr(page, 'reload'):
            try:
                page.reload()
            except Exception:
                pass
        # 切到帮助页时重新检测环境
        if key == 'help' and hasattr(page, 'refresh'):
            try:
                page.refresh()
            except Exception:
                pass
        if key == 'about' and hasattr(page, 'refresh'):
            try:
                page.refresh()
            except Exception:
                pass

    # ---------- 关于页 ----------

    def _build_about(self) -> QWidget:
        from qfluentwidgets import BodyLabel, CardWidget, StrongBodyLabel

        class AboutPage(QWidget):
            """关于页（含环境信息，支持 refresh）。"""

            def __init__(self, config, parent=None):
                super().__init__(parent)
                self.config = config
                self._build()

            def _build(self):
                from .. import __version__
                from ..config import app_dir, config_path
                from ..constants import (
                    CARD_MARGINS, CARD_SPACING, COLOR_ERROR, COLOR_MUTED,
                    COLOR_SUCCESS, COLOR_WARN, PAGE_MARGINS, PAGE_SPACING,
                )

                lay = QVBoxLayout(self)
                lay.setContentsMargins(*PAGE_MARGINS)
                lay.setSpacing(PAGE_SPACING)
                lay.addWidget(SubtitleLabel('关于'))

                # 版本信息
                card = CardWidget()
                cl = QVBoxLayout(card)
                cl.setContentsMargins(*CARD_MARGINS)
                cl.setSpacing(CARD_SPACING)
                cl.addWidget(BodyLabel(f'QQ 群聊总结器  v{__version__}'))
                cl.addWidget(BodyLabel(
                    '拉取 QQ 群聊天记录，AI 总结 + 图片甄选，归档为 Markdown'))
                cl.addWidget(BodyLabel(''))
                cl.addWidget(BodyLabel(f'程序目录：{app_dir()}'))
                cl.addWidget(BodyLabel(f'配置文件：{config_path()}'))
                cl.addWidget(BodyLabel(f'输出目录：{self.config.output_path}'))
                lay.addWidget(card)

                # 环境信息（可刷新）
                lay.addWidget(StrongBodyLabel('环境信息'))
                env_card = CardWidget()
                el = QVBoxLayout(env_card)
                el.setContentsMargins(*CARD_MARGINS)
                el.setSpacing(CARD_SPACING)
                self.lab_qq = BodyLabel('QQ 客户端：检测中...')
                self.lab_napcat = BodyLabel('NapCat：检测中...')
                self.lab_proto = BodyLabel('协议：OneBot v11')
                el.addWidget(self.lab_qq)
                el.addWidget(self.lab_napcat)
                el.addWidget(self.lab_proto)

                from qfluentwidgets import PushButton, FluentIcon as FIF
                row = QHBoxLayout()
                btn = PushButton(FIF.SYNC, '重新检测')
                btn.clicked.connect(self.refresh)
                row.addWidget(btn)
                row.addStretch(1)
                el.addLayout(row)
                lay.addWidget(env_card)

                lay.addWidget(BodyLabel(''))
                lay.addWidget(BodyLabel('依赖：PySide6 + qfluentwidgets + NapCat'))
                lay.addWidget(BodyLabel('日志：logs/app.log'))
                lay.addStretch(1)

            def refresh(self):
                from ..constants import (
                    COLOR_ERROR, COLOR_MUTED, COLOR_SUCCESS, COLOR_WARN,
                )
                from ..core.qqversion import detect_qq, napcat_version

                info = detect_qq()
                if info['installed']:
                    color = COLOR_SUCCESS if info['compatible'] else COLOR_WARN
                    mark = '✓' if info['compatible'] else '⚠'
                    self.lab_qq.setText(
                        f'{mark} QQ 客户端：{info["version"] or "未知"}')
                    self.lab_qq.setTextColor(*color)
                else:
                    self.lab_qq.setText('✗ QQ 客户端：未检测到')
                    self.lab_qq.setTextColor(*COLOR_ERROR)

                nv = napcat_version()
                nd = str(self.config.get('napcat_dir') or '')
                if nv:
                    self.lab_napcat.setText(f'✓ NapCat：v{nv}')
                    self.lab_napcat.setTextColor(*COLOR_SUCCESS)
                elif nd:
                    self.lab_napcat.setText(f'• NapCat：{nd}')
                    self.lab_napcat.setTextColor(*COLOR_MUTED)
                else:
                    self.lab_napcat.setText('✗ NapCat：未找到')
                    self.lab_napcat.setTextColor(*COLOR_ERROR)

        page = AboutPage(self.config)
        return page

    # ---------- 主题 ----------

    def _apply_theme(self) -> None:
        from ..theme import apply_saved_theme
        apply_saved_theme(self.config)

    def apply_theme(self) -> None:
        """外部（设置页）调用以立即切换主题。"""
        self._apply_theme()
        # 调色板切换后，依赖窗口底色的控件需要重绘
        self.update()

    # ---------- 未保存提示 ----------

    def _confirm_leave_settings(self, target_key: str) -> bool:
        """离开设置页前确认。返回 False 表示用户选择留下。

        只在「当前就在设置页」且「有未保存修改」时弹窗。
        """
        if target_key == 'settings':
            return True
        page = self._pages.get('settings')
        if page is None:
            return True
        if self.stack.currentWidget() is not page:
            return True
        try:
            if not page.has_unsaved_changes():
                return True
        except Exception:
            return True

        from qfluentwidgets import MessageBox
        box = MessageBox(
            '有未保存的修改',
            '设置页里有改动还没保存，离开后会丢失。\n要现在保存吗？',
            self)
        box.yesButton.setText('保存并离开')
        box.cancelButton.setText('留在这里')

        if box.exec():
            try:
                page.save_settings()
            except Exception as e:
                self.logger.error(f'保存设置失败: {e}')
            return True
        return False

    def _confirm_close_with_unsaved(self) -> bool:
        """关闭窗口前检查设置页未保存修改。返回 False 表示取消关闭。"""
        page = self._pages.get('settings')
        if page is None:
            return True
        try:
            if not page.has_unsaved_changes():
                return True
        except Exception:
            return True

        from qfluentwidgets import MessageBox
        box = MessageBox(
            '有未保存的修改',
            '设置页里有改动还没保存，退出后会丢失。\n要现在保存吗？',
            self)
        box.yesButton.setText('保存并退出')
        box.cancelButton.setText('取消')

        if box.exec():
            try:
                page.save_settings()
            except Exception as e:
                self.logger.error(f'保存设置失败: {e}')
            return True
        return False

    # ---------- 关闭 ----------

    def closeEvent(self, event) -> None:  # noqa: N802
        """关闭时最小化到托盘（若启用）。"""
        # 真正退出前，检查设置页是否有未保存修改
        if self._force_quit and not self._confirm_close_with_unsaved():
            event.ignore()
            return

        if self.config.get('minimize_to_tray', True) and not self._force_quit:
            event.ignore()
            self.hide()
            tray = getattr(self, 'tray_icon', None)
            if tray is not None:
                try:
                    tray.showMessage(
                        'QQ 群聊总结器', '已最小化到托盘，双击图标可恢复',
                        msecs=2500)
                except Exception:
                    pass
            return
        event.accept()

    _force_quit = False

    def quit_app(self) -> None:
        """真正退出（托盘菜单调用）。"""
        self._force_quit = True
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
