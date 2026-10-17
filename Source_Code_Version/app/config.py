"""配置管理

配置持久化到 config.json（exe 同目录 / 项目根目录）。
API Key 只存在这里，不硬编码进代码、不写日志、不打进 exe。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = 'QQSummarizer'
CONFIG_FILENAME = 'config.json'


def app_dir() -> Path:
    """程序根目录（兼容 PyInstaller 打包后的路径）。

    打包后返回 exe 所在目录（不是 _MEIPASS 临时目录）。
    """
    if getattr(sys, 'frozen', False):
        # 打包后：exe 所在目录
        return Path(sys.executable).parent
    # 开发时：项目根目录（本文件在 app/ 下）
    return Path(__file__).resolve().parent.parent


def _is_writable(d: Path) -> bool:
    """探测目录是否可写。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / '.write_probe'
        probe.write_text('x', encoding='utf-8')
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """数据目录（配置与日志的落点）。

    优先用 exe 同目录（便携：拷走目录就带走配置）。
    若该目录不可写（如放在 Program Files、只读盘），
    回退到 %APPDATA%\\QQSummarizer，保证程序仍能正常工作。
    """
    base = app_dir()
    if _is_writable(base):
        return base
    fallback = Path(os.environ.get('APPDATA') or Path.home()) / APP_NAME
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback


def is_using_fallback_dir() -> bool:
    """当前是否在用 APPDATA 回退目录（供界面提示）。"""
    try:
        return data_dir() != app_dir()
    except Exception:
        return False


def config_path() -> Path:
    return data_dir() / CONFIG_FILENAME


# ---------- NapCat 目录探测 ----------

NAPCAT_DIR_CANDIDATES = (
    'napcat_green',      # 发布包内置的绿色版
    'napcat',            # 简写形式
    'NapCat',            # 大写形式
)


def detect_napcat_dir() -> str:
    """自动探测 NapCat 目录，返回绝对路径字符串。

    探测顺序：
    1. 环境变量 QQSUMMARIZER_NAPCAT_DIR（最高优先级，便于自定义）
    2. 程序同目录下的 napcat_green / napcat / NapCat
    3. 程序上级目录下的同名目录（源码运行时常见布局）
    4. 本机已安装的 C:\\NapCat\\shell* （开发机兜底）
    返回空串表示未找到。
    """
    # 1. 环境变量
    env = os.environ.get('QQSUMMARIZER_NAPCAT_DIR', '').strip()
    if env and Path(env).is_dir():
        return str(Path(env))

    # 2. 程序同目录 / 上级目录
    bases = [app_dir(), app_dir().parent]
    for base in bases:
        for name in NAPCAT_DIR_CANDIDATES:
            cand = base / name
            if _looks_like_napcat(cand):
                return str(cand)

    # 3. 开发机兜底：C:\NapCat\shell41838 之类
    for root in (Path('C:/NapCat'), Path('D:/NapCat')):
        if not root.is_dir():
            continue
        try:
            for sub in sorted(root.iterdir(), reverse=True):
                if _looks_like_napcat(sub):
                    return str(sub)
        except OSError:
            continue
    return ''


def _looks_like_napcat(d: Path) -> bool:
    """判断目录是否是可用的 NapCat 安装。"""
    if not d.is_dir():
        return False
    # 关键文件：启动器 + Hook + 主程序
    needed = ('NapCatWinBootMain.exe', 'NapCatWinBootHook.dll', 'napcat.mjs')
    return all((d / f).exists() for f in needed)


# ---------- 默认值 ----------

DEFAULTS: dict[str, Any] = {
    # AI 配置
    'ai_base': 'http://127.0.0.1:7863/v1',
    'ai_model': 'hy3',
    'ai_key': '',
    'ai_timeout': 1800,
    'ai_max_tokens': 32000,

    # NapCat 配置（目录留空则首次运行自动探测）
    'napcat_api': 'http://127.0.0.1:3000',
    'napcat_dir': '',
    'napcat_webui': 'http://127.0.0.1:6099',

    # 输出
    'output_dir': str(Path.home() / 'Desktop' / 'QQ记录'),

    # 群与账号
    'group_id': '',
    'group_name': '',
    'group_ids': [],          # 多选群
    'qq_account': '',         # 当前选择的账号

    # 总结参数
    'day_start_hour': 12,
    'summary_mode': 'text_image',   # text_image=文字+图片 | text_only=仅文字
    'save_images': True,
    'max_images': 40,
    'img_min_score': 4,
    'img_candidate_cap': 240,
    # 图片处理调优（默认值即可满足绝大多数场景）
    'img_score_batch': 10,          # 视觉评分每批送几张图
    'img_score_thumb_w': 480,       # 送评前缩略图宽度（越小越省 token）
    'img_desc_batch': 6,            # 写图片说明时每批几张

    # 定时任务
    'auto_enabled': False,
    'auto_time': '12:30',           # 每日触发时间
    'auto_group_mode': 'inherit',   # inherit=用群选择 | custom=用 auto_group_ids
    'auto_group_ids': [],
    'auto_catch_up': False,         # 错过是否补跑
    'auto_catch_up_hours': 6,       # 超过多久不补

    # 界面
    'theme': 'auto',                # auto | light | dark
    'minimize_to_tray': True,
    'autostart': False,
    'last_log_lines': 500,
    'first_run_done': False,        # 首次引导是否已完成
}


@dataclass
class Config:
    """运行时配置对象。

    所有模块通过它读取配置，不再直接读模块级常量，
    这样 GUI 改了设置后无需重启程序即可生效。
    """

    data: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    _path: Path | None = None

    # ---- 加载/保存 ----

    @classmethod
    def load(cls, path: Path | None = None) -> 'Config':
        p = Path(path) if path else config_path()
        merged = dict(DEFAULTS)
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding='utf-8'))
                if isinstance(raw, dict):
                    # 只接受已知键，避免旧配置的垃圾字段
                    for k in DEFAULTS:
                        if k in raw:
                            merged[k] = raw[k]
                    # 记录未知键以便排查（不覆盖默认）
                    unknown = [k for k in raw if k not in DEFAULTS]
                    if unknown:
                        merged['_unknown_keys'] = unknown
            except Exception as e:
                raise ConfigError(f'配置文件解析失败: {p}\n{e}') from e

        # NapCat 目录：留空或指向不存在的路径时自动探测
        cur = str(merged.get('napcat_dir') or '').strip()
        if not cur or not Path(cur).exists():
            found = detect_napcat_dir()
            if found:
                merged['napcat_dir'] = found

        obj = cls(data=merged, _path=p)
        return obj

    def save(self, path: Path | None = None) -> None:
        p = Path(path) if path else (self._path or config_path())
        out = {k: v for k, v in self.data.items() if not k.startswith('_')}
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再替换，避免写坏配置
        tmp = p.with_suffix('.json.tmp')
        tmp.write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, p)
        self._path = p

    # ---- 访问 ----

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.data:
            return self.data[key]
        return DEFAULTS.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self.data[k] = v

    # ---- 便捷属性 ----

    @property
    def output_path(self) -> Path:
        return Path(self.get('output_dir'))

    @property
    def napcat_base(self) -> str:
        return str(self.get('napcat_api')).rstrip('/')

    @property
    def ai_base_url(self) -> str:
        return str(self.get('ai_base')).rstrip('/')

    def is_ai_configured(self) -> bool:
        return bool(str(self.get('ai_key')).strip())

    def validate(self) -> list[str]:
        """返回配置问题列表（空表示没问题）。"""
        errs = []
        if not str(self.get('ai_base')).strip():
            errs.append('AI 接口地址为空')
        if not str(self.get('ai_key')).strip():
            errs.append('AI API Key 为空')
        if not str(self.get('ai_model')).strip():
            errs.append('模型名称为空')
        if not str(self.get('napcat_api')).strip():
            errs.append('NapCat 地址为空')
        if not str(self.get('output_dir')).strip():
            errs.append('输出目录为空')
        try:
            h = int(self.get('day_start_hour'))
            if not 0 <= h <= 23:
                errs.append(f'起始小时必须在 0-23（当前 {h}）')
        except (TypeError, ValueError):
            errs.append('起始小时不是有效数字')
        return errs


class ConfigError(Exception):
    """配置相关错误。"""


def mask_key(key: str) -> str:
    """把 API Key 打码，用于界面显示和日志。"""
    s = str(key or '')
    if len(s) <= 8:
        return '*' * len(s)
    return f'{s[:4]}{"*" * (len(s) - 8)}{s[-4:]}'
