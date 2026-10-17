"""图标转换：image.png → app.ico（多尺寸）

源图特点（实测）：
- 1024x1024 正方形，RGB 无 alpha
- 内容有留白：bbox (156,160,932,940)
- 底色米色 (251,239,215)

处理策略：
1. 裁掉外围留白并补成正方形（居中）
2. 生成 6 个标准尺寸：16/32/48/64/128/256
3. 额外导出 256x256 PNG（供 UI 内显示）

用法：
    python make_icon.py [源图] [输出ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# ICO 标准尺寸（Windows 会用最小尺寸做任务栏/资源管理器缩略图）
ICON_SIZES = [16, 32, 48, 64, 128, 256]
PNG_SIZE = 256


def find_bbox(im: Image.Image, bg_tolerance: int = 12) -> tuple[int, int, int, int]:
    """找出非背景内容的外接矩形。

    从四角采样背景色，然后逐像素判断（用 PIL 的 getbbox 思路但支持容差）。
    """
    im = im.convert('RGB')
    w, h = im.size

    # 采样四角确定背景色
    corners = [
        im.getpixel((0, 0)), im.getpixel((w - 1, 0)),
        im.getpixel((0, h - 1)), im.getpixel((w - 1, h - 1)),
    ]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    # 生成 mask：与背景差异超过容差的算内容
    px = im.load()
    left, top, right, bottom = w, h, 0, 0

    # 为提速，按步长扫描（1024 图，步长 2 足够定位边界）
    step = 2
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            if (abs(r - bg[0]) > bg_tolerance
                    or abs(g - bg[1]) > bg_tolerance
                    or abs(b - bg[2]) > bg_tolerance):
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y

    if right <= left or bottom <= top:
        return (0, 0, w, h)   # 全是背景，返回原图范围
    return (left, top, right + 1, bottom + 1)


def make_square(im: Image.Image, bbox: tuple[int, int, int, int],
                pad_ratio: float = 0.04) -> Image.Image:
    """按 bbox 裁剪并补成正方形（带少量内边距）。"""
    cropped = im.crop(bbox)
    w, h = cropped.size
    side = max(w, h)
    pad = int(side * pad_ratio)
    canvas_side = side + pad * 2

    canvas = Image.new('RGB', (canvas_side, canvas_side), (251, 239, 215))
    canvas.paste(cropped, ((canvas_side - w) // 2, (canvas_side - h) // 2))
    return canvas


def convert(src: Path, dst_ico: Path, dst_png: Path | None = None) -> dict:
    """执行转换。"""
    if not src.exists():
        raise SystemExit(f'源图不存在: {src}')

    im = Image.open(src).convert('RGB')
    orig_size = im.size

    bbox = find_bbox(im)
    square = make_square(im, bbox)

    # 生成多尺寸 ICO
    # Pillow 的 save(format='ICO', sizes=[...]) 会自动缩放
    square.save(dst_ico, format='ICO', sizes=[(s, s) for s in ICON_SIZES])

    # 额外导出 PNG（供界面内使用）
    if dst_png:
        big = square.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS)
        big.save(dst_png, format='PNG')

    # 校验
    check = Image.open(dst_ico)
    got_sizes = sorted(check.info.get('sizes', []))

    return {
        'src': str(src), 'src_size': orig_size,
        'bbox': bbox,
        'square_size': square.size,
        'ico': str(dst_ico), 'ico_bytes': dst_ico.stat().st_size,
        'ico_sizes': got_sizes,
        'png': str(dst_png) if dst_png else '',
    }


def main() -> int:
    root = Path(__file__).resolve().parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'image.png'
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else root / 'app.ico'
    png = root / 'app_icon.png'

    print('=' * 56)
    print('图标转换')
    print('=' * 56)
    print(f'源图  : {src}')
    print(f'输出  : {dst}')
    print()

    info = convert(src, dst, png)

    print(f'源图尺寸  : {info["src_size"][0]}x{info["src_size"][1]}')
    print(f'内容边界  : {info["bbox"]}')
    print(f'裁剪后    : {info["square_size"][0]}x{info["square_size"][1]}')
    print(f'ICO 体积  : {info["ico_bytes"] / 1024:.1f} KB')
    print(f'ICO 尺寸  : {[f"{w}x{h}" for w, h in info["ico_sizes"]]}')
    print(f'PNG 输出  : {info["png"]}')
    print()

    # 验证六尺寸齐全
    want = {(s, s) for s in ICON_SIZES}
    got = set(info['ico_sizes'])
    missing = want - got
    if missing:
        print(f'⚠ 缺少尺寸: {sorted(missing)}')
        return 1
    print('✓ 六个标准尺寸齐全')
    return 0


if __name__ == '__main__':
    sys.exit(main())
