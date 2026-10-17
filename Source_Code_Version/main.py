"""QQ 群聊总结器 — 程序入口

启动 GUI；`--minimized` 参数用于开机自启（直接进托盘）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 兼容 PyInstaller：把项目根加入 sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup_high_dpi() -> None:
    """高分屏适配（必须在创建 QApplication 前设置）。"""
    os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '1')


def main() -> int:
    ap = argparse.ArgumentParser(description='QQ 群聊总结器')
    ap.add_argument('--minimized', action='store_true',
                    help='启动后直接最小化到托盘（用于开机自启）')
    ap.add_argument('--config', help='指定配置文件路径')
    args = ap.parse_args()

    _setup_high_dpi()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    # 单实例检测：避免开机自启和手动启动开两个
    from PySide6.QtCore import QSharedMemory
    shared = QSharedMemory('QQSummarizer_SingleInstance')
    if not shared.create(1):
        # 已有实例在跑，提示后退出
        print('程序已在运行中（如需操作请用系统托盘图标）')
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName('QQ 群聊总结器')
    app.setQuitOnLastWindowClosed(False)   # 关窗不退出（最小化到托盘）

    # 应用图标（窗口 + 任务栏 + 对话框统一）
    from app.resources import load_icon
    _ico = load_icon()
    if _ico is not None:
        app.setWindowIcon(_ico)
        # Windows 任务栏正确分组（避免显示成 python.exe 图标）
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'QQSummarizer.Desktop.1')
        except Exception:
            pass

    # 默认字体（中文更清晰）
    font = QFont('Microsoft YaHei UI', 9)
    app.setFont(font)

    # ---- 配置 ----
    from app.config import Config, config_path, is_using_fallback_dir
    cfg_path = Path(args.config) if args.config else None
    try:
        config = Config.load(cfg_path)
    except Exception as e:
        print(f'配置加载失败：{e}')
        return 1

    config_save_error = ''
    if not config_path().exists() and not args.config:
        try:
            config.save()
        except Exception as e:
            # 不再静默失败：记录下来，稍后提示用户
            config_save_error = f'{type(e).__name__}: {e}'

    using_fallback = is_using_fallback_dir()

    # ---- 日志 ----
    from app.logger import get_logger
    logger = get_logger(config)
    logger.register_secret(str(config.get('ai_key', '')))
    logger.info(f'程序启动（配置：{cfg_path or config_path()}）')
    if using_fallback:
        logger.warn('程序目录不可写，配置和日志已改用用户目录保存')
    if config_save_error:
        logger.error(f'配置保存失败：{config_save_error}')

    # ---- 主题 ----
    from app.theme import apply_saved_theme
    apply_saved_theme(config)

    # ---- 主窗口 ----
    from app.gui.main_window import MainWindow
    window = MainWindow(config, logger)

    # ---- 托盘 ----
    from app.tray import TrayIcon
    tray = TrayIcon(window, config, logger)

    # ---- 调度器 ----
    def _on_auto_finished(result) -> None:
        """自动总结完成后的通知。"""
        try:
            if getattr(result, 'ok', False):
                tray.notify(
                    '自动总结完成',
                    f'{result.valid_count} 条发言，{result.image_count} 张图片\n'
                    f'{result.output_file.parent}',
                    6000)
            elif getattr(result, 'cancelled', False):
                tray.notify('自动总结已取消', '', 3000)
            else:
                tray.notify('自动总结失败', str(getattr(result, 'error', ''))[:150], 6000)
        except Exception:
            pass

    from app.scheduler import init_scheduler
    init_scheduler(config, logger, on_finished=_on_auto_finished)

    # ---- 显示 ----
    if args.minimized and config.get('minimize_to_tray', True):
        logger.info('以最小化模式启动（托盘运行）')
        tray.notify('QQ 群聊总结器', '已在后台运行，双击托盘图标可打开', 3000)
    else:
        window.show()

        # 首次启动引导（跳过：指定了 --config 的调试场景）
        if not config.get('first_run_done', False) and not args.config:
            try:
                from app.gui.widgets.first_run import FirstRunDialog
                dlg = FirstRunDialog(config, logger, window)
                dlg.goto_page.connect(window.switch_to)
                # 用户可能中途关闭，用 show 而非 exec 避免阻塞主循环
                dlg.finished.connect(lambda _: logger.info('引导对话框已关闭'))
                dlg.show()
                logger.info('已展示首次启动引导')
            except Exception as e:
                logger.warn(f'首次引导展示失败: {type(e).__name__}: {e}')

    # ---- 退出时清理 ----
    def _cleanup() -> None:
        try:
            from app.scheduler import Scheduler
            s = Scheduler.instance()
            if s is not None:
                s.shutdown()
        except Exception:
            pass
        logger.info('程序退出')

    app.aboutToQuit.connect(_cleanup)

    rc = app.exec()
    return int(rc)


if __name__ == '__main__':
    sys.exit(main())
