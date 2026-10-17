"""定时调度

- 每日定点触发总结（APScheduler）
- 同一天同一任务只跑一次（状态存配置）
- 错过补跑（可选）
- 完成后通知（回调注入，GUI 弹通知）

总结区间固定为「昨天 day_start_hour ~ 今天 day_start_hour」。
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Callable

from .config import Config
from .core.napcat import date_tag, period_bounds
from .core.summarize import SummarizeTask, SummarizeResult
from .logger import Logger

_scheduler: 'Scheduler | None' = None


class Scheduler:
    """全局调度器（单例）。"""

    @classmethod
    def instance(cls) -> 'Scheduler | None':
        return _scheduler

    def __init__(self, config: Config, logger: Logger,
                 on_finished: Callable[[SummarizeResult], None] | None = None):
        self.config = config
        self.logger = logger
        self._on_finished = on_finished
        self._sched = None
        self._lock = threading.Lock()
        self._running = False
        self._should_cancel = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError as e:
            self.logger.warn(f'未安装 apscheduler，定时功能不可用: {e}')
            return

        with self._lock:
            if self._sched is not None:
                return
            self._sched = BackgroundScheduler(
                timezone='Asia/Shanghai',
                job_defaults={'coalesce': True, 'max_instances': 1,
                              'misfire_grace_time': 300})
            self._sched.start()

        self.reschedule()
        self.logger.info('调度器已启动')
        # 错过补跑检查
        if self.config.get('auto_catch_up', False):
            self._maybe_catch_up()

    def shutdown(self) -> None:
        with self._lock:
            if self._sched is not None:
                try:
                    self._sched.shutdown(wait=False)
                except Exception:
                    pass
                self._sched = None
                self.logger.info('调度器已停止')

    def reschedule(self) -> None:
        """按当前配置重新注册任务。"""
        with self._lock:
            if self._sched is None:
                return
            try:
                for job in self._sched.get_jobs():
                    if job.id == 'daily_summary':
                        self._sched.remove_job(job.id)
            except Exception:
                pass

            if not self.config.get('auto_enabled', False):
                return

            try:
                hh, mm = str(self.config.get('auto_time', '12:30')).split(':')
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger(hour=int(hh), minute=int(mm))
                self._sched.add_job(
                    self._run_daily, trigger=trigger, id='daily_summary',
                    replace_existing=True)
                self.logger.info(f'已注册每日任务：{int(hh):02d}:{int(mm):02d}')
            except Exception as e:
                self.logger.error(f'注册定时任务失败: {e}')

    # ---------- 任务执行 ----------

    def run_now(self) -> tuple[bool, str]:
        """立即执行一次（供「立即执行」按钮）。"""
        if self._running:
            return False, '已有任务在执行中'
        threading.Thread(target=self._run_daily, daemon=True,
                         name='summary-now').start()
        return True, '已开始执行，可在「手动总结」页查看进度'

    def _run_daily(self) -> None:
        """执行每日总结。"""
        if self._running:
            self.logger.warn('已有总结任务在执行，跳过本次触发')
            return

        label = self._target_label()
        tag = date_tag(label)
        today = dt.date.today().isoformat()

        # 去重：同一天同一目标周期只跑一次
        st = self.config.get('auto_state') or {}
        if isinstance(st, dict) and st.get('last_date') == tag:
            self.logger.info(f'周期 {tag} 已总结过，跳过本次触发')
            return

        group_id = str(self.config.get('group_id') or '').strip()
        if not group_id:
            self.logger.warn('未配置群号，自动总结跳过')
            return
        group_name = str(self.config.get('group_name') or '').strip() \
            or f'群 {group_id}'

        self._running = True
        self._should_cancel = False
        try:
            start_ts, end_ts = period_bounds(
                label, int(self.config.get('day_start_hour', 12)))
            self.logger.info(
                f'自动总结开始：{dt.datetime.fromtimestamp(start_ts):%Y-%m-%d %H:%M}'
                f' ~ {dt.datetime.fromtimestamp(end_ts):%Y-%m-%d %H:%M}')

            task = SummarizeTask(
                self.config, self.logger,
                should_cancel=lambda: self._should_cancel)
            result = task.run(start_ts, end_ts, label, group_id, group_name)

            # 记录状态（只有成功才标记已完成，失败允许下次重试）
            state = {
                'last_run': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'last_date': tag if result.ok else (st.get('last_date') if isinstance(st, dict) else ''),
                'last_result': 'ok' if result.ok else ('cancelled' if result.cancelled else 'failed'),
                'last_error': result.error or '',
            }
            self.config.set('auto_state', state)
            try:
                self.config.save()
            except Exception:
                pass

            if self._on_finished is not None:
                try:
                    self._on_finished(result)
                except Exception:
                    pass
        except Exception as e:
            self.logger.error(f'自动总结异常: {type(e).__name__}: {e}')
        finally:
            self._running = False

    def _target_label(self) -> dt.date:
        """自动总结的目标日期标签。

        触发时间 >= day_start_hour（默认12）时，总结「昨天」的完整周期。
        触发时间 < day_start_hour 时，今天的周期还没结束，总结「前天」的。
        """
        now = dt.datetime.now()
        h = int(self.config.get('day_start_hour', 12))
        if now.hour >= h:
            return now.date() - dt.timedelta(days=1)
        return now.date() - dt.timedelta(days=2)

    # ---------- 错过补跑 ----------

    def _maybe_catch_up(self) -> None:
        """启动时检查是否需要补跑。"""
        if not self.config.get('auto_enabled', False):
            return
        label = self._target_label()
        tag = date_tag(label)
        st = self.config.get('auto_state') or {}
        if isinstance(st, dict) and st.get('last_date') == tag:
            return   # 已经跑过

        # 检查是否已过今天的触发时间
        try:
            hh, mm = str(self.config.get('auto_time', '12:30')).split(':')
            trigger_today = dt.datetime.now().replace(
                hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            return
        if dt.datetime.now() < trigger_today:
            return   # 今天还没到点，不算错过

        # 限制补跑时间窗
        max_hours = int(self.config.get('auto_catch_up_hours', 6))
        overdue = (dt.datetime.now() - trigger_today).total_seconds() / 3600
        if overdue > max_hours:
            self.logger.info(
                f'错过补跑：距触发已过 {overdue:.1f} 小时，超过 {max_hours} 小时窗口，跳过')
            return

        self.logger.info(f'检测到周期 {tag} 尚未总结，开始补跑')
        threading.Thread(target=self._run_daily, daemon=True,
                         name='summary-catchup').start()

    # ---------- 状态查询 ----------

    @property
    def is_running(self) -> bool:
        return self._running

    def cancel_current(self) -> None:
        self._should_cancel = True

    def next_run_time(self) -> dt.datetime | None:
        with self._lock:
            if self._sched is None:
                return None
            try:
                job = self._sched.get_job('daily_summary')
                if job is not None:
                    return job.next_run_time
            except Exception:
                pass
            return None


def init_scheduler(config: Config, logger: Logger,
                   on_finished: Callable[[SummarizeResult], None] | None = None,
                   ) -> Scheduler:
    """创建并启动全局调度器。"""
    global _scheduler
    _scheduler = Scheduler(config, logger, on_finished)
    _scheduler.start()
    return _scheduler
