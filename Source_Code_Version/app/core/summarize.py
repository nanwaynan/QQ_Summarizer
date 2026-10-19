"""总结编排

把「拉取 → 图片甄选 → AI 总结 → 写文件」串成一条可复用的流程。
CLI 和 GUI 共用同一套逻辑，通过回调获得进度与日志。

调用方式：
    task = SummarizeTask(config, logger)
    result = task.run(start_ts, end_ts, label, group_id, group_name)
"""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..logger import Logger
from . import images as img_mod
from .ai import AIClient, AIError
from .napcat import OneBotClient, NapCatError, date_tag


class Cancelled(Exception):
    """任务被用户取消。"""


@dataclass
class SummarizeResult:
    """总结结果。"""

    ok: bool = False
    output_file: Path | None = None
    images_dir: Path | None = None
    message_count: int = 0
    valid_count: int = 0
    image_count: int = 0
    summary_chars: int = 0
    elapsed: float = 0.0
    completeness: dict = field(default_factory=dict)
    error: str = ''
    cancelled: bool = False
    # 非致命警告（如：模型不支持图片，图片甄选未生效）
    image_warning: str = ''


class SummarizeTask:
    """一次总结任务的执行器。

    所有耗时操作都在当前线程同步执行 —— GUI 需把它放进 QThread。
    """

    def __init__(self, config: Config, logger: Logger,
                 should_cancel: Callable[[], bool] | None = None,
                 on_progress: Callable[[str, float], None] | None = None):
        """
        Args:
            config: 运行时配置
            logger: 日志器（GUI 已注入 sink）
            should_cancel: 返回 True 时尽快中止
            on_progress: (阶段说明, 0-1 进度)，进度为估算值
        """
        self.config = config
        self.logger = logger
        self._cancel = should_cancel or (lambda: False)
        self._progress = on_progress or (lambda msg, pct: None)

    def _log(self, msg: str) -> None:
        self.logger.info(msg)

    def _check_cancel(self) -> None:
        if self._cancel():
            raise Cancelled('任务已取消')

    # ---------- 主流程 ----------

    def run(self, start_ts: float, end_ts: float, label: dt.date,
            group_id: str, group_name: str) -> SummarizeResult:
        import time
        t0 = time.time()
        res = SummarizeResult()
        cfg = self.config

        try:
            period = (f'{dt.datetime.fromtimestamp(start_ts):%Y-%m-%d %H:%M}'
                      f' ~ {dt.datetime.fromtimestamp(end_ts):%Y-%m-%d %H:%M}')
            self._log('=' * 60)
            self._log(f'总结群 {group_id}（{group_name}）')
            self._log(f'时间区间：{period}')
            self._log(f'AI 模型：{cfg.get("ai_model")} @ {cfg.ai_base_url}')
            self._log('=' * 60)

            # ---- 1. 拉取消息 ----
            self._progress('拉取聊天记录', 0.05)
            self._check_cancel()
            client = OneBotClient(cfg, self._log, self._cancel)

            if not client.is_online():
                raise NapCatError(
                    '无法连接 NapCat（端口未响应）。\n'
                    '请先启动服务：双击「启动服务.bat」')

            def _fetch_progress(msg: str, n: int) -> None:
                self._log(f'  {msg}')

            msgs = client.fetch_messages(
                group_id, start_ts, end_ts,
                on_progress=_fetch_progress)
            if not msgs:
                res.error = '区间内没有消息'
                self._log('  区间内没有消息，结束')
                return res

            transcript, valid_count = img_mod.build_transcript(msgs)
            res.message_count = len(msgs)
            res.valid_count = valid_count
            self._log(f'  拉到 {len(msgs)} 条消息，有效发言 {valid_count} 条，'
                      f'{len(transcript)} 字符，耗时 {time.time()-t0:.1f}s')

            # 完整性检查
            comp = client.check_completeness(msgs, start_ts, end_ts)
            res.completeness = comp
            if not comp['complete']:
                self._log('')
                self._log('  ⚠️  数据不完整警告')
                self._log(f'     目标区间：{dt.datetime.fromtimestamp(start_ts):%m-%d %H:%M}'
                          f' ~ {dt.datetime.fromtimestamp(end_ts):%m-%d %H:%M}')
                self._log(f'     实际拿到：{dt.datetime.fromtimestamp(comp["got_lo"]):%m-%d %H:%M}'
                          f' ~ {dt.datetime.fromtimestamp(comp["got_hi"]):%m-%d %H:%M}')
                if comp['head_gap'] > 900:
                    self._log(f'     ⚠️  开头缺 {comp["head_gap"]/3600:.1f} 小时')
                if comp['tail_gap'] > 900:
                    self._log(f'     ⚠️  结尾缺 {comp["tail_gap"]/3600:.1f} 小时')
                self._log('     原因：NapCat 未全程在线，其消息索引不包含离线时段')
                self._log('')

            # ---- 2. 图片 ----
            tag = date_tag(label)
            folder = cfg.output_path / tag
            folder.mkdir(parents=True, exist_ok=True)
            saved_imgs: list[dict] = []
            ai = AIClient(cfg, self._log, self._cancel)

            text_only = str(cfg.get('summary_mode', 'text_image')).lower() == \
                'text_only'
            if text_only:
                self._log('[图片] 当前为「仅文字」模式，跳过图片处理')
            elif cfg.get('save_images', True):
                self._progress('处理群内图片', 0.15)
                saved_imgs = self._process_images(
                    ai, msgs, folder, group_name, res)

            # ---- 3. AI 总结 ----
            self._progress('AI 总结中', 0.55)
            self._check_cancel()
            self._log('[总结] 调用 AI...')
            t_sum = time.time()
            summary = ai.summarize(transcript, period, group_name)
            res.summary_chars = len(summary)
            self._log(f'  完成，{len(summary)} 字符，耗时 {time.time()-t_sum:.1f}s')

            # ---- 4. 写文件 ----
            self._progress('写入文件', 0.95)
            out_file = folder / f'{tag}.md'
            img_section = img_mod.images_markdown(saved_imgs)

            # 头部元信息：仅文字模式标注模式，不写图片计数（否则会显示 0 张）
            if text_only:
                img_meta = '> 总结模式：仅文字'
            else:
                img_meta = f'> 群内图片：{len(saved_imgs)} 张'

            out_file.write_text(f"""# {group_name} 群聊总结 - {label.isoformat()}

> 群号：{group_id}
> 时间区间：{period}
> 生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}
> 有效发言：{valid_count} 条
{img_meta}
> AI 模型：{cfg.get('ai_model')}

---

{summary}
{img_section}
---

## 附：原始聊天记录

<details>
<summary>点击展开完整记录（{valid_count} 条）</summary>

```
{transcript}
```

</details>
""", encoding='utf-8')

            res.ok = True
            res.output_file = out_file
            res.images_dir = (folder / 'images') if saved_imgs else None
            res.image_count = len(saved_imgs)
            res.elapsed = time.time() - t0
            self._progress('完成', 1.0)
            self._log(f'  已保存: {out_file}')
            if saved_imgs:
                self._log(f'  图片目录: {folder / "images"}')
            self._log(f'总结完成，总耗时 {res.elapsed:.0f}s')
            return res

        except Cancelled:
            res.cancelled = True
            res.error = '已取消'
            self._log('任务已取消')
            return res
        except (NapCatError, AIError) as e:
            res.error = str(e)
            self._log(f'错误: {e}')
            return res
        except Exception as e:
            import traceback
            res.error = f'{type(e).__name__}: {e}'
            self._log(f'未预期的错误: {res.error}')
            self._log(traceback.format_exc()[-1500:])
            return res

    # ---------- 图片子流程 ----------

    def _process_images(self, ai: AIClient, msgs: list[dict], folder: Path,
                        group_name: str, res: SummarizeResult) -> list[dict]:
        import shutil as _sh
        import time as _t
        cfg = self.config
        t_img = _t.time()

        cands = img_mod.collect_images(msgs)
        self._log(f'[图片] 候选 {len(cands)} 张（已去重、跳过表情包）')
        if not cands:
            self._log('  没有需要保存的图片')
            return []

        tmp_dir = folder / '_tmp_images'
        if tmp_dir.exists():
            _sh.rmtree(tmp_dir, ignore_errors=True)

        self._check_cancel()
        downloaded = img_mod.download_images(
            cands, folder, subdir='_tmp_images',
            should_cancel=self._cancel)
        self._log(f'  已下载 {len(downloaded)} 张，耗时 {_t.time()-t_img:.1f}s')
        if not downloaded:
            _sh.rmtree(tmp_dir, ignore_errors=True)
            return []

        # AI 评分
        self._progress('AI 甄选图片', 0.3)
        scored = ai.score_images(
            downloaded, group_name,
            batch_size=int(cfg.get('img_score_batch', 10)),
            thumb_width=int(cfg.get('img_score_thumb_w', 480)),
            max_candidates=int(cfg.get('img_candidate_cap', 240)),
        )

        # 挑选
        picked = img_mod.select_images(
            scored,
            limit=int(cfg.get('max_images', 40)),
            min_score=int(cfg.get('img_min_score', 4)))
        hi = sum(1 for x in picked if x.get('score', 0) >= int(cfg.get('img_min_score', 4)))
        self._log(f'  甄选出 {len(picked)} 张（其中高价值 {hi} 张）')

        # ---- 模型不支持图片输入的提示 ----
        # 评分全批失败 + 没有任何一张拿到真实分数 → 基本可以判定模型不支持视觉。
        # 这时图片会被"补足"逻辑全部选入（分数默认 3），数量看起来正常但没有甄选效果。
        vision_err = getattr(ai, 'last_vision_error', '')
        if vision_err and hi == 0 and scored:
            self._log('')
            self._log('  ⚠ 图片甄选未生效：AI 模型可能不支持图片输入')
            self._log(f'     最后一次错误：{vision_err}')
            self._log('     后果：图片按默认分 3 全部保留，没有经过真正的价值筛选')
            self._log('     建议：')
            self._log('       · 换一个支持视觉的模型（如 gpt-4o / qwen-vl-plus / glm-4v）')
            self._log('       · 或在设置里把「总结模式」改为「仅文字」，跳过图片处理')
            res.image_warning = (
                '图片甄选未生效：当前 AI 模型可能不支持图片输入。\n'
                f'错误：{vision_err[:120]}\n'
                '建议换支持视觉的模型，或把总结模式改为「仅文字」。')
            self._log('')

        # 移动 + 清理临时目录
        moved = img_mod.move_selected(picked, folder, subdir='images')
        _sh.rmtree(tmp_dir, ignore_errors=True)

        # 写说明
        if moved:
            self._progress('AI 写图片说明', 0.45)
            self._log('  让 AI 写图片说明...')
            t_desc = _t.time()
            ai.describe_images(
                moved, batch_size=int(cfg.get('img_desc_batch', 6)))
            self._log(f'  说明完成，耗时 {_t.time()-t_desc:.1f}s')

        res.image_count = len(moved)
        return moved
