"""日志模块

设计要点：
- 统一日志出口，支持**回调注入**（GUI 拿日志显示到界面，CLI 打到控制台）
- 同时落盘到 logs/app.log（带轮转）
- 敏感信息（API Key）自动打码
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Iterable

from .config import Config, data_dir, mask_key

LEVELS = ('DEBUG', 'INFO', 'WARN', 'ERROR')


class LogRecord:
    """一条日志记录。"""

    __slots__ = ('time', 'level', 'text', 'source')

    def __init__(self, level: str, text: str, source: str = ''):
        self.time = dt.datetime.now()
        self.level = level
        self.text = text
        self.source = source

    @property
    def stamp(self) -> str:
        return self.time.strftime('%H:%M:%S')

    def format(self, with_date: bool = False) -> str:
        t = self.time.strftime('%Y-%m-%d %H:%M:%S' if with_date else '%H:%M:%S')
        src = f'[{self.source}] ' if self.source else ''
        return f'[{t}] {self.level:5s} {src}{self.text}'

    def __repr__(self) -> str:
        return f'<LogRecord {self.format()}>'


class Logger:
    """全局日志器。

    用法：
        logger = Logger(config)
        logger.add_sink(gui_callback)     # GUI 接管
        logger.info('开始总结')

    线程安全：内部用 RLock 保护回调列表和文件写入。
    """

    def __init__(self, config: Config | None = None, memory_size: int = 2000):
        self._config = config
        self._sinks: list[Callable[[LogRecord], None]] = []
        self._memory: deque[LogRecord] = deque(maxlen=memory_size)
        self._lock = threading.RLock()
        self._log_file: Path | None = None
        self._file_handler: logging.Handler | None = None
        self._redact: set[str] = set()      # 需要打码的敏感串

        # 控制台输出（CLI 模式）
        self._console = True

        # 根据配置初始化文件日志
        if config is not None:
            self._setup_file_logging()

    # ---------- 敏感信息处理 ----------

    def register_secret(self, value: str) -> None:
        """把一个敏感串登记进来，之后所有日志里出现它都会被替换。"""
        v = str(value or '').strip()
        if len(v) >= 8:
            with self._lock:
                self._redact.add(v)

    def _scrub(self, text: str) -> str:
        with self._lock:
            secrets = tuple(self._redact)
        for s in secrets:
            if s in text:
                text = text.replace(s, mask_key(s))
        return text

    # ---------- 文件日志 ----------

    def _setup_file_logging(self) -> None:
        try:
            # data_dir() 会在 exe 目录不可写时回退到 APPDATA
            log_dir = data_dir() / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = log_dir / 'app.log'
        except Exception:
            self._log_file = None
            return

    def _write_file(self, rec: LogRecord) -> None:
        if not self._log_file:
            return
        try:
            # 简单轮转：超过 5MB 就改名
            if self._log_file.exists() and self._log_file.stat().st_size > 5 * 1024 * 1024:
                old = self._log_file.with_suffix('.log.1')
                try:
                    if old.exists():
                        old.unlink()
                    self._log_file.rename(old)
                except Exception:
                    pass
            with self._log_file.open('a', encoding='utf-8') as f:
                f.write(rec.format(with_date=True) + '\n')
        except Exception:
            pass   # 日志失败不影响主流程

    # ---------- 输出 ----------

    def emit(self, level: str, text: str, source: str = '') -> None:
        rec = LogRecord(level, self._scrub(str(text)), source)
        with self._lock:
            self._memory.append(rec)
            sinks = list(self._sinks)
        for cb in sinks:
            try:
                cb(rec)
            except Exception:
                pass   # 回调异常不能影响主流程
        if self._console and not sinks:
            # 有 GUI 接管时不重复打到控制台
            print(rec.format(), flush=True)
        elif self._console:
            print(rec.format(), flush=True)
        self._write_file(rec)

    # 便捷方法
    def debug(self, text: str, source: str = '') -> None:
        self.emit('DEBUG', text, source)

    def info(self, text: str, source: str = '') -> None:
        self.emit('INFO', text, source)

    def warn(self, text: str, source: str = '') -> None:
        self.emit('WARN', text, source)

    def error(self, text: str, source: str = '') -> None:
        self.emit('ERROR', text, source)

    def success(self, text: str, source: str = '') -> None:
        """语义化：成功也是 INFO 级，但界面可以绿色显示。"""
        self.emit('INFO', text, source)

    # ---------- 订阅 ----------

    def add_sink(self, callback: Callable[[LogRecord], None]) -> None:
        with self._lock:
            if callback not in self._sinks:
                self._sinks.append(callback)

    def remove_sink(self, callback: Callable[[LogRecord], None]) -> None:
        with self._lock:
            if callback in self._sinks:
                self._sinks.remove(callback)

    def set_console(self, enabled: bool) -> None:
        self._console = bool(enabled)

    # ---------- 历史 ----------

    def recent(self, count: int = 200, min_level: str = 'DEBUG') -> list[LogRecord]:
        try:
            idx = LEVELS.index(min_level.upper())
        except ValueError:
            idx = 0
        with self._lock:
            items = list(self._memory)
        return [r for r in items if LEVELS.index(r.level) >= idx][-count:]

    def clear_memory(self) -> None:
        with self._lock:
            self._memory.clear()

    def export(self, path: Path, min_level: str = 'DEBUG') -> int:
        recs = self.recent(10_000, min_level)
        path.write_text(
            '\n'.join(r.format(with_date=True) for r in recs),
            encoding='utf-8')
        return len(recs)


# ---------- 全局单例 ----------

_logger: Logger | None = None
_logger_lock = threading.Lock()


def get_logger(config: Config | None = None) -> Logger:
    """获取全局 logger（首次调用时创建）。"""
    global _logger
    with _logger_lock:
        if _logger is None:
            _logger = Logger(config)
        elif config is not None:
            _logger._config = config
            if _logger._log_file is None:
                _logger._setup_file_logging()
        return _logger


def reset_logger() -> None:
    """重置全局 logger（测试用）。"""
    global _logger
    with _logger_lock:
        _logger = None


class LogSinkThread(threading.Thread):
    """把 logger 记录转发到队列的适配线程（可选，用于批量刷新）。"""

    def __init__(self, logger: Logger, out_queue, level: str = 'DEBUG'):
        super().__init__(daemon=True)
        self.logger = logger
        self.queue = out_queue
        self.level = level
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(0.2)

    def stop(self) -> None:
        self._stop.set()
