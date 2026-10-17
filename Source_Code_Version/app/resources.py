"""资源路径解析

打包后（PyInstaller）资源分布在两处：
- onefile: 运行时解压到 sys._MEIPASS
- onedir:  在 <exe目录>/_internal

开发时则在项目根目录。

本模块统一处理，让图标等资源在任何形态下都能找到。
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """资源目录（含 app.ico 等打包进去的资源）。"""
    # PyInstaller 打包后（onefile 与 onedir 都会设置 _MEIPASS）
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass)
    # 开发时：项目根（本文件在 app/ 下）
    return Path(__file__).resolve().parent.parent


def icon_path() -> Path:
    """应用图标（app.ico）的完整路径。

    查找顺序：
    1. 打包内资源目录
    2. exe / 项目同目录（便于用户替换图标）
    """
    from .config import app_dir

    candidates = [
        resource_dir() / 'app.ico',
        app_dir() / 'app.ico',
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]   # 不存在也返回第一个，调用方自行判断


def icon_png_path() -> Path:
    """PNG 版图标（界面内显示用，尺寸统一）。"""
    from .config import app_dir

    for p in (resource_dir() / 'app_icon.png', app_dir() / 'app_icon.png'):
        if p.exists():
            return p
    return resource_dir() / 'app_icon.png'


def load_icon():
    """加载 QIcon（找不到文件时返回 None，调用方用内置图标兜底）。"""
    try:
        from PySide6.QtGui import QIcon
        p = icon_path()
        if p.exists():
            ico = QIcon(str(p))
            if not ico.isNull():
                return ico
    except Exception:
        pass
    return None
