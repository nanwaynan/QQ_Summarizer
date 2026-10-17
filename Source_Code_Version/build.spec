# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

要点：
- 用 onedir（文件夹）而非 onefile：启动快（1-3秒 vs 5-15秒），
  且大幅降低 Windows Defender 误报概率
- 排除未使用的 Qt 模块（WebEngine/Qt3D/QtCharts 等），控制体积
- 包含 qfluentwidgets 的资源文件（图标、字体）

打包命令：
    .venv\\Scripts\\pyinstaller.exe build.spec --noconfirm
输出：
    dist\\QQ总结器\\QQ总结器.exe
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()   # noqa: F821  (SPECPATH 由 PyInstaller 注入)

# ---------- 数据文件 ----------

datas = []

# 应用图标（运行时窗口/托盘要用，必须打进包）
for _name in ('app.ico', 'app_icon.png'):
    _p = ROOT / _name
    if _p.exists():
        datas.append((str(_p), '.'))

# qfluentwidgets 的资源（图标、QSS 等）
try:
    datas += collect_data_files('qfluentwidgets', include_py_files=False)
except Exception:
    pass

# exe 图标：文件不在就传 None，PyInstaller 会用默认图标而不是报错
_icon = ROOT / 'app.ico'
_icon_arg = str(_icon) if _icon.exists() else None

# ---------- 隐藏导入 ----------
# 这些模块是运行时动态导入的（懒加载），PyInstaller 静态分析看不到

hiddenimports = [
    'app.gui.pages.manual',
    'app.gui.pages.auto',
    'app.gui.pages.settings',
    'app.gui.pages.logs',
    'app.scheduler',
    'app.tray',
    'app.autostart',
    'apscheduler.schedulers.background',
    'apscheduler.triggers.cron',
    'tzlocal',
    'PIL.Image',
    'PIL.ImageQt',
]

# ---------- 排除项 ----------
# 剔掉体积大且用不到的 Qt 模块

excludes = [
    # Qt 大模块
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineQuick', 'PySide6.QtWebChannel', 'PySide6.QtWebSockets',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DAnimation',
    'PySide6.Qt3DExtras', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQuickControls2',
    'PySide6.QtQml', 'PySide6.QtQmlModels',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
    'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
    'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtTest',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtSql',
    # 注意：QtSvg / QtSvgWidgets 不能排除 —— qfluentwidgets 依赖它们渲染图标
    'PySide6.QtRemoteObjects', 'PySide6.QtScxml',
    'PySide6.QtSensors', 'PySide6.QtSpatialAudio',
    # 其他大依赖
    'matplotlib', 'numpy', 'pandas', 'scipy',
    'tkinter', 'unittest', 'pydoc_data',
    'IPython', 'jupyter', 'notebook',
]

# ---------- 分析 ----------

a = Analysis(   # noqa: F821
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)   # noqa: F821

exe = EXE(   # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QQ总结器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # 不用 UPX：降低杀软误报
    console=False,          # GUI 程序，不显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_arg,
)

coll = COLLECT(   # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='QQ总结器',
)
