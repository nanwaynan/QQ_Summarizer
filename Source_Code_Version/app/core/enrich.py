"""消息富化：展开折叠转发、语音转文字

背景：OneBot 的 `message` 里，折叠转发只是一个 `{type:"forward", data:{id}}`
占位段，语音是 `{type:"record", data:{file}}`。原实现把它们替换成
`[合并转发]` / `[语音]` 四个字，内容全部丢失。

本模块负责在生成 transcript 之前补全这些内容：

1. **折叠转发展开**：调 `get_forward_msg` 取回被转发的每一条消息，
   递归展开嵌套转发（NapCat 不直接递归，必须对每个内层 id 再调一次）。
2. **语音转文字**：调 NapCat 的 `fetch_ptt_text`（QQ 自己的语音转写），
   拿到文字。拿不到就降级成 `[语音 Ns]`。

设计要点（很重要）：

- **不能在解析循环里同步发请求**：一个群里可能有几十条转发/语音，
  逐条同步请求会让总结慢到无法接受。做法是：
  「先扫描收集 id → 批量请求 → 回填内容 → 再生成文本」。
- **全局限流 + 失败熔断**：接口挂了就停止尝试，避免几百次超时叠加。
- **结果全部缓存**：同一 id 只请求一次（转发链里常有重复）。
- **失败必须降级**：任何异常都不能让整条总结失败，最差退回占位文字。
"""

from __future__ import annotations

import time
from typing import Callable

# 转发最多展开几层（防止恶意/超深嵌套把内存打爆）
MAX_FORWARD_DEPTH = 3

# 单个转发里最多取多少条（防止超长转发拖慢总结）
MAX_FORWARD_ITEMS = 200

# 一次总结里最多展开多少个转发 / 转写多少条语音
MAX_FORWARD_TOTAL = 300
MAX_VOICE_TOTAL = 200

# 连续失败这么多次就熔断，后面不再尝试
FAIL_LIMIT = 5

# 接口调用超时（秒）
CALL_TIMEOUT = 20


class Enricher:
    """消息富化器：把折叠转发和语音补成可读文本。

    用法：
        en = Enricher(call, log=..., expand_forward=True, transcribe_voice=True)
        en.prepare(msgs)              # 批量取回所有需要的内容（会联网）
        text = en.render_text(m)      # 解析单条消息（纯本地，可用缓存）
    """

    def __init__(self, call: Callable[..., object], log: Callable[[str], None] | None = None,
                 expand_forward: bool = True, transcribe_voice: bool = True,
                 should_cancel: Callable[[], bool] | None = None):
        """
        Args:
            call: OneBot 调用函数，签名 call(action, payload, timeout=...) -> data
            log: 日志回调
            expand_forward: 是否展开折叠转发
            transcribe_voice: 是否把语音转成文字
            should_cancel: 返回 True 时尽快中止
        """
        self._call = call
        self._log = log or (lambda m: None)
        self._cancel = should_cancel or (lambda: False)

        self.expand_forward = expand_forward
        self.transcribe_voice = transcribe_voice

        # 缓存：forward id -> 解析好的条目列表；message_id -> 语音文字
        self._forward_cache: dict[str, list[dict] | None] = {}
        self._voice_cache: dict[str, str] = {}

        self._fail_count = 0
        self._broken = False

        # 统计（日志用）
        self.stats = {
            'forward_expanded': 0,
            'forward_failed': 0,
            'voice_transcribed': 0,
            'voice_failed': 0,
            'voice_fallback': 0,
        }

    # ---------- 准备阶段（联网）----------

    def prepare(self, msgs: list[dict]) -> None:
        """扫描所有消息，批量取回转发内容与语音文字。

        调用一次即可；之后 render_text 全部走缓存，不再联网。
        """
        if not (self.expand_forward or self.transcribe_voice):
            return

        forward_ids, voice_items = self._scan(msgs)

        if self.expand_forward and forward_ids:
            self._log(f'  发现 {len(forward_ids)} 个折叠转发，正在展开...')
            for fid in forward_ids[:MAX_FORWARD_TOTAL]:
                if self._cancel():
                    return
                self._expand_one(fid, depth=1)
            self._log_forward_summary()

        if self.transcribe_voice and voice_items:
            self._log(f'  发现 {len(voice_items)} 条语音，正在转文字...')
            for mid, _ in voice_items[:MAX_VOICE_TOTAL]:
                if self._cancel():
                    return
                self._transcribe_one(mid)
            self._log_voice_summary()

    def _scan(self, msgs: list[dict]) -> tuple[list[str], list[tuple[str, dict]]]:
        """收集需要处理的 forward id 和语音消息。"""
        forward_ids: list[str] = []
        seen_f: set[str] = set()
        voices: list[tuple[str, dict]] = []
        seen_v: set[str] = set()

        for m in msgs:
            mid = str(m.get('message_id') or '')
            for seg in self._segments(m):
                t = seg.get('type')
                d = seg.get('data') or {}

                if t == 'forward' and self.expand_forward:
                    fid = str(d.get('id') or d.get('content') or '')
                    if fid and fid not in seen_f:
                        seen_f.add(fid)
                        forward_ids.append(fid)

                elif t == 'record' and self.transcribe_voice:
                    if mid and mid not in seen_v:
                        seen_v.add(mid)
                        voices.append((mid, d))

        return forward_ids, voices

    # ---------- 折叠转发 ----------

    def _expand_one(self, fid: str, depth: int) -> list[dict] | None:
        """展开一个转发 id（带缓存、层数限制、熔断）。"""
        if fid in self._forward_cache:
            return self._forward_cache[fid]
        if depth > MAX_FORWARD_DEPTH or self._broken:
            self._forward_cache[fid] = None
            return None

        try:
            data = self._call('get_forward_msg',
                              {'message_id': fid, 'id': fid},
                              timeout=CALL_TIMEOUT)
        except Exception as e:
            self.stats['forward_failed'] += 1
            self._bump_fail(f'展开转发失败：{type(e).__name__}: {str(e)[:60]}')
            self._forward_cache[fid] = None
            return None

        items = self._normalize_forward(data)
        if items is None:
            self.stats['forward_failed'] += 1
            self._forward_cache[fid] = None
            return None

        self._fail_count = 0
        self.stats['forward_expanded'] += 1
        items = items[:MAX_FORWARD_ITEMS]

        # 递归展开内层转发（NapCat 不自动递归，必须自己对内层 id 再调）
        for it in items:
            for seg in it.get('_segments') or []:
                if seg.get('type') == 'forward':
                    inner_id = str((seg.get('data') or {}).get('id') or '')
                    if inner_id:
                        inner = self._expand_one(inner_id, depth + 1)
                        if inner is not None:
                            seg['_expanded'] = inner

        self._forward_cache[fid] = items
        return items

    def _normalize_forward(self, data: object) -> list[dict] | None:
        """把 get_forward_msg 的返回统一成
        [{'name': str, 'text': str, '_segments': [...]}, ...]

        兼容两种形态：
        - {'messages': [OB11Message, ...]}    （NapCat 实际返回）
        - {'message': [OB11Message, ...]}     （OneBot 标准）
        """
        if not isinstance(data, dict):
            return None
        raw = data.get('messages')
        if raw is None:
            raw = data.get('message')
        if not isinstance(raw, list):
            return None

        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue

            # 形态 A：完整 OB11 消息
            if item.get('message') is not None and item.get('sender') is not None:
                segs = item.get('message') or []
                name = self._sender_of(item)
            # 形态 B：node 模板（{type:"node", data:{user_id,nickname,message,content}}）
            elif item.get('type') == 'node':
                d = item.get('data') or {}
                segs = d.get('message') or d.get('content') or []
                name = str(d.get('nickname') or d.get('user_id') or '未知')
            # 形态 C：直接是 {user_id, nickname, message}
            elif item.get('nickname') is not None or item.get('user_id') is not None:
                segs = item.get('message') or []
                name = str(item.get('nickname') or item.get('user_id') or '未知')
            else:
                continue

            if isinstance(segs, str):
                segs = [{'type': 'text', 'data': {'text': segs}}]
            if not isinstance(segs, list):
                continue

            out.append({
                'name': name,
                'text': self._plain_text(segs),
                '_segments': segs,
            })
        return out

    @staticmethod
    def _sender_of(msg: dict) -> str:
        s = msg.get('sender') or {}
        return str(s.get('card') or s.get('nickname')
                   or s.get('user_id') or msg.get('user_id') or '未知')

    # ---------- 语音转文字 ----------

    def _transcribe_one(self, message_id: str) -> None:
        """把一条语音转成文字（带缓存、熔断）。"""
        if message_id in self._voice_cache or self._broken:
            return
        try:
            data = self._call('fetch_ptt_text',
                              {'message_id': message_id},
                              timeout=CALL_TIMEOUT)
        except Exception as e:
            self.stats['voice_failed'] += 1
            self._bump_fail(f'语音转文字失败：{type(e).__name__}: {str(e)[:60]}')
            return

        text = ''
        if isinstance(data, dict):
            text = str(data.get('text') or '').strip()
        elif isinstance(data, str):
            text = data.strip()

        if text:
            self._fail_count = 0
            self._voice_cache[message_id] = text
            self.stats['voice_transcribed'] += 1
        else:
            # 接口通了但没内容（QQ 那边可能还没转好）
            self.stats['voice_failed'] += 1

    # ---------- 渲染 ----------

    def render_text(self, msg: dict) -> str:
        """把一条消息转成可读文本（含展开后的转发/语音文字）。

        纯本地操作：所有联网都在 prepare() 阶段完成。
        """
        return self._render_segments(
            self._segments(msg),
            frame=None,
            msg_id=str(msg.get('message_id') or ''),
        ).strip()

    def _render_segments(self, segs: list[dict], frame: str | None,
                         msg_id: str = '') -> str:
        """渲染消息段数组。转发内容用 `frame` 前缀做视觉分层。"""
        parts: list[str] = []

        for seg in segs:
            if not isinstance(seg, dict):
                continue
            t = seg.get('type')
            d = seg.get('data') or {}

            if t == 'text':
                parts.append(str(d.get('text', '')))

            elif t == 'at':
                qq = d.get('qq')
                parts.append('@全体成员' if qq == 'all' else f'@{qq}')

            elif t == 'image':
                parts.append('[图片]')

            elif t == 'face':
                parts.append('[表情]')

            elif t == 'video':
                parts.append('[视频]')

            elif t == 'file':
                parts.append(f"[文件:{d.get('name', '')}]")

            elif t == 'forward':
                parts.append(self._render_forward(seg))

            elif t == 'record':
                # 语音转写按「所属消息」的 message_id 查缓存
                parts.append(self._render_record(seg, msg_id=msg_id))

        txt = ''.join(parts)

        # 转发内容缩进显示，让 AI 能看出层级
        if frame:
            txt = f'{frame}{txt}'
        return txt

    def _render_forward(self, seg: dict) -> str:
        """渲染一个折叠转发段。

        格式（多行，便于 AI 分清层级）：
            [合并转发 ↧]
             张三: xxx
             李四: yyy
            [↥ 合并转发结束]
        """
        d = seg.get('data') or {}
        fid = str(d.get('id') or '')

        # 优先用 prepare 阶段展开好并挂上去的内容
        items = seg.get('_expanded')
        if items is None and fid:
            items = self._forward_cache.get(fid)

        if not items:
            return ' [合并转发] '

        lines = ['[合并转发 ↧]']
        for it in items:
            name = it.get('name') or '未知'
            body = it.get('text') or ''

            # 内层嵌套转发：把占位符替换成实际内容
            for iseg in it.get('_segments') or []:
                if iseg.get('type') == 'forward':
                    inner = iseg.get('_expanded')
                    if inner:
                        inner_txt = self._render_forward_items(inner)
                        if inner_txt:
                            body = body.replace('[内层转发]',
                                                f'[内层转发: {inner_txt}]', 1)
                        break

            if body:
                lines.append(f' {name}: {body}')
        lines.append('[↥ 合并转发结束]')
        return ' ' + ' '.join(lines) + ' '

    def _render_forward_items(self, items: list[dict]) -> str:
        """渲染内层转发（第二层及以上）。"""
        out = []
        for it in items:
            name = it.get('name') or '未知'
            body = it.get('text') or ''
            if body:
                out.append(f'{name}: {body}')
        return ' ; '.join(out)

    def _render_record(self, seg: dict, msg_id: str = '') -> str:
        """渲染语音段：有转写用文字，否则带时长。"""
        d = seg.get('data') or {}

        text = self._voice_cache.get(str(msg_id or ''))
        if text:
            return f'[语音转文字:{text}]'

        # 降级：显示时长（NapCat 一般不给，能拿到就用）
        secs = d.get('duration') or d.get('time')
        try:
            s = int(float(secs))
            if 0 < s < 3600:
                return f'[语音 {s}s]'
        except (TypeError, ValueError):
            pass
        return '[语音]'

    # ---------- 工具 ----------

    @staticmethod
    def _segments(msg: dict) -> list[dict]:
        """取消息段数组；兼容字符串形式的 message。"""
        m = msg.get('message')
        if isinstance(m, list):
            return m
        if isinstance(m, str) and m:
            return [{'type': 'text', 'data': {'text': m}}]
        return []

    def _plain_text(self, segs: list[dict]) -> str:
        """转发条目内部的纯文本（不再递归展开内层转发，避免重复）。"""
        parts = []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            t = seg.get('type')
            d = seg.get('data') or {}
            if t == 'text':
                parts.append(str(d.get('text', '')))
            elif t == 'at':
                qq = d.get('qq')
                parts.append('@全体成员' if qq == 'all' else f'@{qq}')
            elif t == 'image':
                parts.append('[图片]')
            elif t == 'video':
                parts.append('[视频]')
            elif t == 'file':
                parts.append(f"[文件:{d.get('name', '')}]")
            elif t == 'record':
                parts.append('[语音]')
            elif t == 'forward':
                parts.append('[内层转发]')
        return ' '.join(p.strip() for p in parts if p.strip())

    def _bump_fail(self, msg: str) -> None:
        """累计失败次数，超限熔断。"""
        self._fail_count += 1
        if self._fail_count == 1:
            self._log(f'  {msg}')
        if self._fail_count >= FAIL_LIMIT and not self._broken:
            self._broken = True
            self._log(f'  连续失败 {self._fail_count} 次，'
                      f'后续转发/语音将按占位文字处理')

    def _log_forward_summary(self) -> None:
        s = self.stats
        if s['forward_expanded'] or s['forward_failed']:
            self._log(f'  折叠转发展开：成功 {s["forward_expanded"]} 个，'
                      f'失败 {s["forward_failed"]} 个')

    def _log_voice_summary(self) -> None:
        s = self.stats
        if s['voice_transcribed'] or s['voice_failed']:
            self._log(f'  语音转文字：成功 {s["voice_transcribed"]} 条，'
                      f'未转成 {s["voice_failed"]} 条')


def enrich_messages(msgs: list[dict], call: Callable[..., object],
                    log: Callable[[str], None] | None = None,
                    expand_forward: bool = True,
                    transcribe_voice: bool = True,
                    should_cancel: Callable[[], bool] | None = None,
                    ) -> Enricher:
    """便捷入口：建一个 Enricher 并完成 prepare。

    返回的 Enricher 可直接用于 render_text / stats。
    """
    en = Enricher(call, log=log,
                  expand_forward=expand_forward,
                  transcribe_voice=transcribe_voice,
                  should_cancel=should_cancel)
    en.prepare(msgs)
    return en
