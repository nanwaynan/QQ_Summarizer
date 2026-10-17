"""AI 客户端

封装 OpenAI 兼容接口的调用：
- 文本总结（分块 + 汇总）
- 多模态：图片评分、图片描述
- 重试 / 空响应检测 / 截断提示
- 支持取消
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from . import prompts
from .prompts import CHUNK_PROMPT, DESCRIBE_PROMPT, FINAL_PROMPT, SCORE_PROMPT


class AIError(Exception):
    """AI 调用失败。"""


class AIClient:
    """OpenAI 兼容 AI 客户端（配置动态读取，支持运行时改配置）。"""

    def __init__(self, config: Config, log: Callable[[str], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None):
        self.config = config
        self._log = log or (lambda m: None)
        self._should_cancel = should_cancel or (lambda: False)

    # ---------- 底层调用 ----------

    def chat(self, messages: list[dict], max_tokens: int | None = None,
             temperature: float = 0.3, timeout: int | None = None,
             retries: int = 3) -> str:
        """发一次 chat 请求，返回正文文本。

        含重试与空响应检测（推理模型的 reasoning_tokens 会吃掉 max_tokens，
        配额不足时 finish_reason=length 且正文为空）。
        """
        max_tokens = max_tokens or int(self.config.get('ai_max_tokens', 32000))
        timeout = timeout or int(self.config.get('ai_timeout', 1800))
        base = self.config.ai_base_url
        model = str(self.config.get('ai_model', ''))
        key = str(self.config.get('ai_key', ''))

        if not key:
            raise AIError('未配置 AI API Key，请到「设置」里填写')

        last_err: str = ''
        for attempt in range(1, retries + 1):
            if self._should_cancel():
                raise AIError('已取消')

            body = json.dumps({
                'model': model,
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': temperature,
            }).encode()
            req = urllib.request.Request(
                f'{base}/chat/completions', data=body,
                headers={
                    'Authorization': f'Bearer {key}',
                    'Content-Type': 'application/json',
                },
                method='POST',
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors='replace')[:400]
                last_err = f'HTTP {e.code}: {detail}'
                self._log(f'      AI 调用失败（{attempt}/{retries}）: {last_err[:150]}')
                time.sleep(2 * attempt)
                continue
            except Exception as e:
                last_err = f'{type(e).__name__}: {e}'
                self._log(f'      AI 调用异常（{attempt}/{retries}）: {last_err[:150]}')
                time.sleep(2 * attempt)
                continue

            choice = (data.get('choices') or [{}])[0]
            content = ((choice.get('message') or {}).get('content') or '').strip()
            finish = choice.get('finish_reason')
            usage = data.get('usage') or {}
            reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')

            if content:
                if finish == 'length':
                    self._log(f'      注意：输出达到长度上限（推理 {reasoning} tokens），可能被截断')
                return content

            last_err = f'返回空内容（finish_reason={finish}, reasoning_tokens={reasoning}）'
            self._log(f'      空响应（{attempt}/{retries}）: {last_err}')
            time.sleep(2 * attempt)

        raise AIError(f'AI 调用失败（已重试 {retries} 次）: {last_err}')

    # ---------- 连接测试 ----------

    def test_connection(self) -> dict:
        """测试连接，返回 {'ok': bool, 'latency': float, 'model': str, 'reply': str, 'error': str}"""
        t0 = time.time()
        try:
            reply = self.chat(
                [{'role': 'user', 'content': prompts.CONNECTION_TEST_PROMPT}],
                max_tokens=2000, timeout=60, retries=1)
            return {
                'ok': True,
                'latency': time.time() - t0,
                'model': str(self.config.get('ai_model', '')),
                'reply': reply[:100],
                'error': '',
            }
        except Exception as e:
            return {
                'ok': False,
                'latency': time.time() - t0,
                'model': str(self.config.get('ai_model', '')),
                'reply': '',
                'error': str(e)[:300],
            }

    # ---------- 文本总结 ----------

    def summarize(self, transcript: str, period: str, group_name: str,
                  chunk_chars: int = 22000) -> str:
        """分块总结 + 汇总。"""
        if len(transcript) <= chunk_chars:
            self._log(f'  记录 {len(transcript)} 字符，单次总结')
            return self.chat([{
                'role': 'user',
                'content': CHUNK_PROMPT.format(
                    group_name=group_name, period=period, idx=1, total=1,
                    count='若干', transcript=transcript),
            }])

        chunks = self._split_chunks(transcript, chunk_chars)
        self._log(f'  记录较长，分 {len(chunks)} 块分别总结')

        partials: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            if self._should_cancel():
                raise AIError('已取消')
            n = chunk.count('\n') + 1
            self._log(f'    第 {i}/{len(chunks)} 块（{len(chunk)} 字符）...')
            text = self.chat([{
                'role': 'user',
                'content': CHUNK_PROMPT.format(
                    group_name=group_name, period=period, idx=i,
                    total=len(chunks), count=n, transcript=chunk),
            }])
            partials.append(text)
            self._log(f'      完成（{len(text)} 字符）')

        self._log(f'  汇总 {len(partials)} 段摘要...')
        combined = '\n\n'.join(
            f'### 第 {i} 段摘要\n{p}' for i, p in enumerate(partials, 1))
        return self.chat([{
            'role': 'user',
            'content': FINAL_PROMPT.format(
                group_name=group_name, period=period,
                total=len(partials), summaries=combined),
        }], max_tokens=12000)

    @staticmethod
    def _split_chunks(text: str, limit: int) -> list[str]:
        """按行切块，尽量不切断单条消息。"""
        lines = text.split('\n')
        chunks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for line in lines:
            if cur_len + len(line) > limit and cur:
                chunks.append('\n'.join(cur))
                cur, cur_len = [], 0
            cur.append(line)
            cur_len += len(line) + 1
        if cur:
            chunks.append('\n'.join(cur))
        return chunks

    # ---------- 图片：评分 ----------

    def score_images(self, imgs: list[dict], group_name: str,
                     batch_size: int = 10, thumb_width: int = 480,
                     max_candidates: int = 240,
                     on_progress: Callable[[int, int], None] | None = None,
                     ) -> list[dict]:
        """给候选图片打分（原地写 img['score'] / img['reason']）。"""
        if not imgs:
            return imgs
        if len(imgs) > max_candidates:
            step = len(imgs) / max_candidates
            imgs = [imgs[int(i * step)] for i in range(max_candidates)]

        total_batches = (len(imgs) + batch_size - 1) // batch_size
        self._log(f'    AI 评选中（{len(imgs)} 张，每批 {batch_size} 张）...')

        for bi, start in enumerate(range(0, len(imgs), batch_size), 1):
            if self._should_cancel():
                for img in imgs[start:]:
                    img.setdefault('score', 3)
                    img.setdefault('reason', '已取消')
                raise AIError('已取消')

            batch = imgs[start:start + batch_size]
            content = [{'type': 'text',
                        'text': SCORE_PROMPT.format(group_name=group_name)}]

            for i, img in enumerate(batch, 1):
                ctx = []
                if img.get('ctx_before'):
                    ctx.append('上文：' + ' / '.join(img['ctx_before']))
                if img.get('text'):
                    ctx.append('本条：' + img['text'])
                if img.get('ctx_after'):
                    ctx.append('下文：' + ' / '.join(img['ctx_after']))
                ctx_txt = '  '.join(ctx)[:300] or '（无文字上下文）'

                content.append({
                    'type': 'text',
                    'text': f'【第 {i} 张】发送者 {img["sender"]}，时间 {img["time"]}\n{ctx_txt}',
                })
                b64 = make_thumb_b64(Path(img['local']), thumb_width)
                if not b64:
                    img['score'] = 2
                    img['reason'] = '无法读取'
                    continue
                content.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
                })

            try:
                reply = self.chat([{'role': 'user', 'content': content}],
                                  max_tokens=3000, timeout=900)
            except AIError as e:
                if '已取消' in str(e):
                    raise
                self._log(f'      评分失败: {str(e)[:100]}')
                for img in batch:
                    img.setdefault('score', 3)
                    img.setdefault('reason', '评分异常')
                continue

            got = {}
            for line in reply.splitlines():
                m = re.match(r'^(\d+)\s*[|｜]\s*(\d+)\s*(?:[|｜]\s*(.*))?$', line.strip())
                if m:
                    got[int(m.group(1))] = (int(m.group(2)), (m.group(3) or '').strip())
            for i, img in enumerate(batch, 1):
                if i in got:
                    img['score'], img['reason'] = got[i]
                else:
                    img['score'] = 3
                    img['reason'] = ''
            avg = sum(v[0] for v in got.values()) / max(1, len(got))
            self._log(f'      已评 {len(got)}/{len(batch)} 张（本批均分 {avg:.1f}）')
            if on_progress:
                on_progress(bi, total_batches)

        return imgs

    # ---------- 图片：描述 ----------

    def describe_images(self, imgs: list[dict], batch_size: int = 6,
                        on_progress: Callable[[int, int], None] | None = None) -> None:
        """给图片写一句话说明（原地写 img['desc']）。"""
        if not imgs:
            return
        total_batches = (len(imgs) + batch_size - 1) // batch_size

        for bi, start in enumerate(range(0, len(imgs), batch_size), 1):
            if self._should_cancel():
                return
            batch = imgs[start:start + batch_size]
            content = [{'type': 'text', 'text': DESCRIBE_PROMPT}]

            for i, img in enumerate(batch, 1):
                try:
                    raw = Path(img['local']).read_bytes()
                except Exception:
                    continue
                if len(raw) > 4 * 1024 * 1024:
                    content.append({'type': 'text', 'text': f'{i}|（图片过大，略）'})
                    continue
                b64 = base64.b64encode(raw).decode()
                ext = Path(img['local']).suffix.lower()
                mime = {'.png': 'image/png', '.gif': 'image/gif',
                        '.webp': 'image/webp'}.get(ext, 'image/jpeg')
                content.append({'type': 'text', 'text': f'第 {i} 张：'})
                content.append({'type': 'image_url',
                                'image_url': {'url': f'data:{mime};base64,{b64}'}})

            try:
                reply = self.chat([{'role': 'user', 'content': content}],
                                  max_tokens=2000, timeout=600)
            except AIError as e:
                if '已取消' in str(e):
                    return
                self._log(f'    图片描述失败: {str(e)[:100]}')
                continue

            got = {}
            for line in reply.splitlines():
                m = re.match(r'^(\d+)\s*[|｜]\s*(.+)$', line.strip())
                if m:
                    got[int(m.group(1))] = m.group(2).strip()
            for i, img in enumerate(batch, 1):
                img['desc'] = got.get(i, '')
            self._log(f'    已描述 {len(got)}/{len(batch)} 张')
            if on_progress:
                on_progress(bi, total_batches)


def make_thumb_b64(path: Path, width: int = 480) -> str | None:
    """生成缩略图的 base64（评分用）。PIL 不可用时回退原图。"""
    try:
        from PIL import Image
        im = Image.open(path)
        im = im.convert('RGB')
        w, h = im.size
        if w > width:
            im = im.resize((width, int(h * width / w)))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=72)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 1_500_000:
                return None
            return base64.b64encode(raw).decode()
        except Exception:
            return None
