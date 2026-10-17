"""消息与图片处理

- 消息 → 可读文本转换
- 图片候选收集 / 下载 / 挑选
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Callable

MIN_IMAGE_BYTES = 3000


# ---------- 消息文本 ----------

def extract_text(msg: dict) -> str:
    """把一条 OneBot 消息转成可读文本。"""
    parts: list[str] = []
    for seg in msg.get('message') or []:
        if not isinstance(seg, dict):
            continue
        t = seg.get('type')
        d = seg.get('data') or {}
        if t == 'text':
            parts.append(str(d.get('text', '')))
        elif t == 'at':
            qq = d.get('qq')
            parts.append(f'@{qq}' if qq and qq != 'all' else '@全体成员')
        elif t == 'image':
            parts.append('[图片]')
        elif t == 'forward':
            parts.append('[合并转发]')
        elif t == 'file':
            parts.append(f"[文件:{d.get('name', '')}]")
        elif t == 'video':
            parts.append('[视频]')
        elif t == 'record':
            parts.append('[语音]')
    return re.sub(r'\s+', ' ', ''.join(parts)).strip()


def sender_name(msg: dict) -> str:
    s = msg.get('sender') or {}
    return str(s.get('card') or s.get('nickname') or msg.get('user_id') or '未知')


def build_transcript(msgs: list[dict]) -> tuple[str, int]:
    """生成 '[时间] 昵称: 内容' 的逐行记录。"""
    lines: list[str] = []
    for m in msgs:
        text = extract_text(m)
        if not text:
            continue
        ts = dt.datetime.fromtimestamp(m.get('time', 0)).strftime('%m-%d %H:%M')
        lines.append(f'[{ts}] {sender_name(m)}: {text}')
    return '\n'.join(lines), len(lines)


def count_messages(msgs: list[dict]) -> tuple[int, int]:
    """返回 (总有消息数, 有效发言数)。"""
    valid = sum(1 for m in msgs if extract_text(m))
    return len(msgs), valid


# ---------- 图片候选 ----------

def collect_images(msgs: list[dict], min_bytes: int = MIN_IMAGE_BYTES,
                   ) -> list[dict]:
    """收集候选图片，附上下文（供 AI 甄选）。

    - 只保留 sub_type=0 的普通图片（跳过动画表情/贴纸）
    - 按 file（MD5）去重
    - 记录该图前后各 2 条消息的文字作为上下文
    """
    lines_by_idx: dict[int, str] = {}
    for i, m in enumerate(msgs):
        t = extract_text(m)
        if t:
            ts = dt.datetime.fromtimestamp(m.get('time', 0)).strftime('%H:%M')
            lines_by_idx[i] = f'{sender_name(m)}: {t[:120]}'

    out: list[dict] = []
    seen: set[str] = set()
    for i, m in enumerate(msgs):
        ts = dt.datetime.fromtimestamp(m.get('time', 0)).strftime('%m-%d %H:%M')
        name = sender_name(m)
        for seg in m.get('message') or []:
            if not isinstance(seg, dict) or seg.get('type') != 'image':
                continue
            d = seg.get('data') or {}
            if d.get('sub_type') not in (0, None):
                continue
            url = d.get('url')
            if not url:
                continue
            fname = str(d.get('file') or '')
            if fname and fname in seen:
                continue
            if fname:
                seen.add(fname)
            try:
                size = int(d.get('file_size') or 0)
            except (TypeError, ValueError):
                size = 0
            if 0 < size < min_bytes:
                continue

            ctx_before = [lines_by_idx[j] for j in range(max(0, i - 2), i)
                          if j in lines_by_idx]
            ctx_after = [lines_by_idx[j] for j in range(i + 1, min(len(msgs), i + 3))
                         if j in lines_by_idx]

            out.append({
                'url': url,
                'file': fname,
                'size': size,
                'time': ts,
                'sender': name,
                'text': extract_text(m),
                'ctx_before': ctx_before,
                'ctx_after': ctx_after,
            })
    return out


def download_images(imgs: list[dict], folder: Path, subdir: str = 'images',
                    min_bytes: int = MIN_IMAGE_BYTES,
                    on_progress: Callable[[int, int], None] | None = None,
                    should_cancel: Callable[[], bool] | None = None,
                    ) -> list[dict]:
    """下载图片到 folder/subdir/，返回成功项（含 local/rel/bytes）。

    注意：图片 URL 带时效凭证，必须当场下载。
    """
    if not imgs:
        return []
    cancel = should_cancel or (lambda: False)
    out_dir = folder / subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    ok: list[dict] = []
    total = len(imgs)
    for i, img in enumerate(imgs, 1):
        if cancel():
            break
        ext = '.jpg'
        fname = str(img.get('file') or '')
        if '.' in fname:
            cand = '.' + fname.rsplit('.', 1)[-1].lower()
            if cand in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                ext = cand
        safe_ts = str(img.get('time', '')).replace('-', '').replace(':', '') \
            .replace(' ', '_')
        dest = out_dir / f'{i:03d}_{safe_ts}{ext}'
        try:
            req = urllib.request.Request(
                img['url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if len(data) < min_bytes:
                continue
            dest.write_bytes(data)
            item = dict(img)
            item['local'] = dest
            item['rel'] = f'{subdir}/{dest.name}'
            item['bytes'] = len(data)
            ok.append(item)
        except Exception:
            pass
        if on_progress and i % 20 == 0:
            on_progress(i, total)
    if on_progress:
        on_progress(total, total)
    return ok


def select_images(imgs: list[dict], limit: int = 40,
                  min_score: int = 4) -> list[dict]:
    """按 AI 评分挑选：高分优先，不足则按分数补足，最后按时间排序。"""
    if not imgs:
        return []
    for x in imgs:
        if x.get('score') is None:
            x['score'] = 3
            x['reason'] = x.get('reason', '')

    good = sorted([x for x in imgs if x['score'] >= min_score],
                  key=lambda x: (-x['score'], x.get('time', '')))
    picked = good[:limit]

    if len(picked) < limit:
        rest = sorted([x for x in imgs if x['score'] < min_score],
                      key=lambda x: (-x['score'], x.get('time', '')))
        picked += rest[:limit - len(picked)]

    picked.sort(key=lambda x: x.get('time', ''))
    return picked


def move_selected(imgs: list[dict], folder: Path, subdir: str = 'images',
                  ) -> list[dict]:
    """把选中的图片从临时目录移到正式目录，重命名并清理临时目录。"""
    out_dir = folder / subdir
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    moved: list[dict] = []
    for i, img in enumerate(imgs, 1):
        src = Path(img.get('local', ''))
        if not src.exists():
            continue
        tail = src.stem.split('_', 1)[-1] if '_' in src.stem else src.stem
        dst = out_dir / f'{i:02d}_{tail}{src.suffix}'
        try:
            shutil.move(str(src), str(dst))
            item = dict(img)
            item['local'] = dst
            item['rel'] = f'{subdir}/{dst.name}'
            moved.append(item)
        except Exception:
            continue
    return moved


def images_markdown(imgs: list[dict]) -> str:
    """生成 Markdown 图片章节。"""
    if not imgs:
        return ''
    lines = ['', '---', '', f'## 🖼️ 群内图片（AI 甄选 {len(imgs)} 张）', '',
             '> 按重要性筛选，附 AI 说明；点图可看原图。', '']
    for img in imgs:
        desc = img.get('desc') or ''
        reason = img.get('reason') or ''
        score = img.get('score')
        lines.append(f'### {img.get("time", "")} · {img.get("sender", "")}')
        lines.append('')
        if desc:
            lines.append(f'> {desc}')
            lines.append('')
        if reason or score:
            meta = []
            if score:
                meta.append(f'价值 {score}/5')
            if reason:
                meta.append(reason)
            lines.append(f'*({" · ".join(meta)})*')
            lines.append('')
        lines.append(f'![{desc or "图片"}]({img.get("rel", "")})')
        lines.append('')
    return '\n'.join(lines)
