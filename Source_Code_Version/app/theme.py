"""主题应用

统一处理四件事 —— 缺任何一件都会出现「部分区域仍是深色」的不一致：

1. qfluentwidgets 的 `setTheme()`  —— 决定各控件 QSS（卡片、按钮、输入框等）
2. `setThemeColor()`              —— 主题强调色
3. QPalette + `setColorScheme()`  —— 调色板、原生标题栏、半透明控件的底色
4. `_refresh_widgets()`           —— 强制重绘已有窗口

为什么需要第 3、4 点（v1.0.3 以前只有前两点，导致「切了主题还是黑的」）：

- `CardWidget` 的背景是**半透明白**（浅色下 `rgba(255,255,255,170)`），
  它会透出父级底色。父级链是：
  `CardWidget → 页面 QWidget → QStackedWidget → MainWindow`。
  这些中间层都不画自己的背景，最终透出 `QPalette::Window`。
- `ScrollArea` 的 QSS 里写着 `background: transparent`，其**视口**同样透明，
  而 qfluentwidgets 给 QScrollArea 的调色板 Window 是纯黑 `#000000`。
  于是浅色主题下卡片透出黑色 → 呈现深灰。

因此：① 必须显式给应用设置 QPalette；② 必须把页面和主窗口的底色
用 `setBackgroundColor()` 直接刷成主题色，不能指望继承。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from .constants import ACCENT_COLOR

__all__ = ['apply_theme', 'apply_saved_theme', 'bg_color', 'paint_bg']


def bg_color(dark: bool | None = None):
    """当前主题下的页面底色（QColor）。

    dark=None 时向 qfluentwidgets 查询**当前实际**主题，
    不能用固定值 —— 否则深浅切换后底色会落后一个状态。
    """
    from PySide6.QtGui import QColor

    if dark is None:
        try:
            from qfluentwidgets import isDarkTheme
            dark = bool(isDarkTheme())
        except Exception:
            dark = False
    return QColor('#202020' if dark else '#f3f3f3')


def paint_bg(widget, dark: bool | None = None) -> None:
    """给控件刷上主题底色（用于页面根容器/主窗口/滚动区视口）。"""
    if widget is None:
        return
    try:
        widget.setAutoFillBackground(True)
        pal = widget.palette()
        pal.setColor(widget.backgroundRole(), bg_color(dark))
        widget.setPalette(pal)
    except Exception:
        pass


def apply_theme(mode: str, refresh: bool = True) -> None:
    """应用主题。

    Args:
        mode: 'light' | 'dark' | 'auto'（其他值按 auto 处理）
        refresh: 是否强制刷新已存在的窗口
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

    if refresh:
        _refresh_widgets()


def _apply_color_scheme(mode: str) -> None:
    """同步 Qt 调色板与原生标题栏。"""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return

    # ---- 1. 系统配色方案（原生标题栏 / 系统控件）----
    hints = app.styleHints()
    if hints is not None and hasattr(hints, 'setColorScheme'):
        try:
            if mode == 'light':
                hints.setColorScheme(Qt.ColorScheme.Light)
            elif mode == 'dark':
                hints.setColorScheme(Qt.ColorScheme.Dark)
            else:
                if hasattr(hints, 'unsetColorScheme'):
                    hints.unsetColorScheme()
        except Exception:
            pass

    # ---- 2. 应用调色板（页面/卡片的实际底色来源）----
    try:
        app.setPalette(_build_palette(_resolve_is_dark(mode)))
    except Exception:
        pass


def _resolve_is_dark(mode: str) -> bool:
    """把 mode 解析成实际的明暗（auto 时向 qfluentwidgets 要结果）。"""
    if mode == 'dark':
        return True
    if mode == 'light':
        return False
    try:
        from qfluentwidgets import isDarkTheme
        return bool(isDarkTheme())
    except Exception:
        return False


def _build_palette(dark: bool):
    """构造与 Fluent 主题一致的调色板。"""
    from PySide6.QtGui import QColor, QPalette

    p = QPalette()
    if dark:
        window, base, alt = QColor('#202020'), QColor('#2d2d2d'), QColor('#1f1f1f')
        text, disabled, btn, tooltip = (
            QColor('#ffffff'), QColor('#7a7a7a'), QColor('#2d2d2d'), QColor('#2b2b2b'))
    else:
        window, base, alt = QColor('#f3f3f3'), QColor('#ffffff'), QColor('#f9f9f9')
        text, disabled, btn, tooltip = (
            QColor('#000000'), QColor('#9e9e9e'), QColor('#fbfbfb'), QColor('#f9f9f9'))

    highlight = QColor(ACCENT_COLOR)

    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, btn)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, tooltip)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Highlight, highlight)
    p.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    p.setColor(QPalette.PlaceholderText, disabled)
    p.setColor(QPalette.Link, highlight)

    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Base, window)
    p.setColor(QPalette.Disabled, QPalette.Button, btn)
    return p


def _refresh_widgets() -> None:
    """强制所有已存在窗口按新主题重绘。

    除了 repolish，还要重新刷页面底色（见 paint_bg 的说明）。
    """
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None:
        return

    dark = _resolve_is_dark('auto')
    for w in app.topLevelWidgets():
        if not isinstance(w, QWidget):
            continue
        try:
            _repolish(w)
            w.update()
        except Exception:
            continue

    # 主窗口里的页面/滚动区需要重新上色
    try:
        from .gui.main_window import MainWindow
        for w in app.topLevelWidgets():
            if isinstance(w, MainWindow):
                w.restyle_backgrounds()
    except Exception:
        pass


def _repolish(root: QWidget) -> None:
    """递归 unpolish/polish，让控件重新解析当前主题的 QSS。"""
    stack = [root]
    while stack:
        w = stack.pop()
        try:
            st = w.style()
            st.unpolish(w)
            st.polish(w)
            w.update()
        except Exception:
            pass
        try:
            stack.extend(w.findChildren(QWidget))
        except Exception:
            pass


def apply_saved_theme(config) -> str:
    """从配置读取并应用主题，返回实际生效的模式。"""
    mode = str(config.get('theme', 'auto')).lower()
    apply_theme(mode)
    return mode
