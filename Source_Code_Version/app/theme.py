"""主题应用

统一处理三件事 —— 缺任何一件都会出现「部分区域仍是深色」的不一致：

1. qfluentwidgets 的 `setTheme()`  —— 决定各控件 QSS（卡片、按钮、输入框等）
2. `setThemeColor()`              —— 主题强调色
3. `QStyleHints.setColorScheme()` —— Qt 调色板 + Windows 原生标题栏

第 3 点是关键。`setTheme()` 只替换 QSS，**不碰 QApplication 的调色板**。而顶层
QWidget 的背景是用 `QPalette::Window` 填充的，紧凑态的 NavigationInterface
面板 QSS 又是 `background-color: transparent`，于是会透出窗口底色调色板 ——
在系统为深色时，选「浅色」主题就会出现「内容区浅色、左侧导航仍深色」。
原生标题栏由 DWM 绘制，同样不受 `setTheme()` 影响，需要
`setColorScheme()` 才能让沉浸式深色标志跟着切换。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from .constants import ACCENT_COLOR

__all__ = ['apply_theme', 'apply_saved_theme']


def apply_theme(mode: str) -> None:
    """应用主题。

    Args:
        mode: 'light' | 'dark' | 'auto'（其他值按 auto 处理）
    """
    from qfluentwidgets import Theme, setTheme, setThemeColor

    mode = str(mode or 'auto').lower()
    theme = {'light': Theme.LIGHT, 'dark': Theme.DARK}.get(mode, Theme.AUTO)

    try:
        setTheme(theme)
        setThemeColor(ACCENT_COLOR)
    except Exception:
        pass

    _apply_color_scheme(mode)


def _apply_color_scheme(mode: str) -> None:
    """同步 Qt 调色板与原生标题栏。

    该 API 需要 Qt 6.5+；旧版本上没有也不该影响程序启动，所以整体兜底。
    """
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return
    hints = app.styleHints()
    if hints is None or not hasattr(hints, 'setColorScheme'):
        return

    try:
        if mode == 'light':
            hints.setColorScheme(Qt.ColorScheme.Light)
        elif mode == 'dark':
            hints.setColorScheme(Qt.ColorScheme.Dark)
        else:
            # 跟随系统：交回给平台默认值
            if hasattr(hints, 'unsetColorScheme'):
                hints.unsetColorScheme()
    except Exception:
        pass


def apply_saved_theme(config) -> str:
    """从配置读取并应用主题，返回实际生效的模式。"""
    mode = str(config.get('theme', 'auto')).lower()
    apply_theme(mode)
    return mode
