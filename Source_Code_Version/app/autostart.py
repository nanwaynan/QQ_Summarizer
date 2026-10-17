"""开机自启

写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
（当前用户级，不需要管理员权限）
"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'QQSummarizer'


def _target_command() -> str:
    """要写入注册表的启动命令。"""
    if getattr(sys, 'frozen', False):
        # 打包后直接指向 exe
        return f'"{sys.executable}" --minimized'
    # 开发模式：用 pythonw + main.py
    py = Path(sys.executable)
    pyw = py.with_name('pythonw.exe')
    exe = pyw if pyw.exists() else py
    main = Path(__file__).resolve().parent.parent / 'main.py'
    return f'"{exe}" "{main}" --minimized'


def set_autostart(enabled: bool) -> bool:
    """开启/关闭开机自启，返回是否成功。"""
    try:
        import winreg
    except ImportError:
        return False

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    except OSError:
        return False

    try:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ,
                              _target_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        return False
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass


def is_autostart_enabled() -> bool:
    """检查是否已设置了开机自启。"""
    try:
        import winreg
    except ImportError:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_QUERY_VALUE)
        try:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def current_autostart_command() -> str:
    """读取注册表里记录的命令（用于诊断）。"""
    try:
        import winreg
    except ImportError:
        return ''
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_QUERY_VALUE)
        try:
            val, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(val)
        except FileNotFoundError:
            return ''
        finally:
            winreg.CloseKey(key)
    except OSError:
        return ''
