"""界面常量

统一管理主题色、间距、语义色，避免各处硬编码。
"""

from __future__ import annotations

# ---------- 主题色 ----------

ACCENT_COLOR = '#0067c0'      # 全局强调色（按钮、选中态）

# ---------- 间距（统一布局观感）----------

PAGE_MARGINS = (24, 20, 24, 20)     # 页面根边距
PAGE_SPACING = 14                    # 页面根间距
CARD_MARGINS = (20, 16, 20, 16)      # 卡片内边距
CARD_SPACING = 10                    # 卡片内间距

# ---------- 语义色（浅色, 深色）----------

COLOR_SUCCESS = ('#107c10', '#6ccb5f')      # 成功/已连接
COLOR_WARN = ('#9d5d00', '#ffd166')         # 警告/等待
COLOR_ERROR = ('#c42b1c', '#ff99a4')        # 错误/断开
COLOR_MUTED = ('#8a8a8a', '#9e9e9e')        # 次要/未知

# 日志级别色（浅色, 深色）
LOG_LEVEL_COLORS: dict[str, tuple[str, str]] = {
    'ERROR': COLOR_ERROR,
    'WARN': COLOR_WARN,
    'INFO': ('#000000', '#ffffff'),
    'DEBUG': COLOR_MUTED,
}


def level_color(level: str, is_dark: bool = False) -> str:
    """取某个日志级别在当前主题下的颜色。"""
    pair = LOG_LEVEL_COLORS.get(level.upper())
    if not pair:
        return '#ffffff' if is_dark else '#000000'
    return pair[1] if is_dark else pair[0]
