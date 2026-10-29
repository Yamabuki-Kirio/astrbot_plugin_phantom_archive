"""把「自带纯黑底」的角色立绘抠成透明。

部分素材来自社区站点，导出时把黑底一起烤进了图里：浅色卡片上会显示成
一整块黑色矩形，跟纸面完全不搭。这里只处理**与画面边框连通的近黑像素**：

- 人物内部的黑色（头发、外套、鞋）不与边框连通，保持原样；
- 已经是透明底的立绘不受影响（边框不满足「不透明 + 近黑」）；
- 背景图 `background.webp` 是模糊底衬，不处理。

用法：

    python scripts/key_out_dark_background.py            # 直接改写素材
    python scripts/key_out_dark_background.py --dry-run  # 只报告会改哪些
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CHARACTER_DIR = PLUGIN_ROOT / "data" / "assets" / "characters"

# 每个通道都不超过这个值才算「近黑」
BLACK_THRESHOLD = 24
# 判定为「黑底图」的条件：四边采样点里近黑且不透明的比例
DARK_EDGE_RATIO = 0.8
# 抠图后的边缘羽化半径，避免锯齿
FEATHER_RADIUS = 0.6
# 输出质量：带 alpha 的 WebP 会略大于原图，靠质量换体积
OUTPUT_QUALITY = 78


def _near_black_mask(rgba: Image.Image, threshold: int = BLACK_THRESHOLD) -> Image.Image:
    """近黑且不透明的像素 = 255。"""
    red, green, blue, alpha = rgba.split()
    max_rgb = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    near_black = max_rgb.point(lambda value: 255 if value <= threshold else 0)
    opaque = alpha.point(lambda value: 255 if value > 0 else 0)
    return ImageChops.multiply(near_black, opaque)


def _border_connected(mask: Image.Image) -> Image.Image:
    """从四条边向内 flood fill，标出「与边框连通」的近黑区域（值 128）。"""
    work = mask.copy()
    width, height = work.size
    for x in range(width):
        for y in (0, height - 1):
            if work.getpixel((x, y)) == 255:
                ImageDraw.floodfill(work, (x, y), 128)
    for y in range(height):
        for x in (0, width - 1):
            if work.getpixel((x, y)) == 255:
                ImageDraw.floodfill(work, (x, y), 128)
    return work


def _looks_like_dark_background(image: Image.Image) -> bool:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    step = max(1, min(width, height) // 40)
    samples: list[tuple[int, int, int, int]] = []
    for x in range(0, width, step):
        samples.append(rgba.getpixel((x, 0)))
        samples.append(rgba.getpixel((x, height - 1)))
    for y in range(0, height, step):
        samples.append(rgba.getpixel((0, y)))
        samples.append(rgba.getpixel((width - 1, y)))
    dark = [
        pixel
        for pixel in samples
        if pixel[3] > 200 and max(pixel[0], pixel[1], pixel[2]) <= BLACK_THRESHOLD
    ]
    return bool(samples) and len(dark) / len(samples) >= DARK_EDGE_RATIO


def key_out(path: Path) -> bool:
    """处理一张图，返回是否真的改动过。"""
    with Image.open(path) as source:
        image = source.convert("RGBA")
    if not _looks_like_dark_background(image):
        return False

    mask = _border_connected(_near_black_mask(image))
    width, height = image.size
    alpha = Image.new("L", (width, height), 255)
    alpha_pixels = alpha.load()
    mask_pixels = mask.load()
    for y in range(height):
        for x in range(width):
            if mask_pixels[x, y] == 128:
                alpha_pixels[x, y] = 0

    if FEATHER_RADIUS > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(FEATHER_RADIUS))

    result = image.copy()
    result.putalpha(alpha)
    buffer = path.with_suffix(".tmp.webp")
    result.save(buffer, format="WEBP", quality=OUTPUT_QUALITY, method=6)
    buffer.replace(path)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抠掉角色立绘里烤进图的黑底")
    parser.add_argument("--dry-run", action="store_true", help="只列出会被处理的文件")
    parser.add_argument("--root", default=str(CHARACTER_DIR), help="角色素材目录")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"素材目录不存在：{root}", file=sys.stderr)
        return 1

    targets = sorted(
        path
        for path in root.rglob("*.webp")
        if path.name != "background.webp"
    )
    changed: list[tuple[Path, int, int]] = []
    skipped = 0
    for path in targets:
        before = path.stat().st_size
        if args.dry_run:
            with Image.open(path) as probe:
                hit = _looks_like_dark_background(probe.convert("RGBA"))
            if hit:
                changed.append((path, before, before))
            else:
                skipped += 1
            continue
        if key_out(path):
            changed.append((path, before, path.stat().st_size))
        else:
            skipped += 1

    total_before = sum(item[1] for item in changed)
    total_after = sum(item[2] for item in changed)
    for path, before, after in changed:
        delta = after - before
        print(f"{path.relative_to(root).as_posix():<44} {before // 1024:>5} KB → {after // 1024:>5} KB ({delta // 1024:+d})")

    print()
    print(f"处理 {len(changed)} 张，跳过 {skipped} 张（已透明或不是黑底）")
    print(
        f"这些文件合计 {total_before / 1024 / 1024:.2f} MB → {total_after / 1024 / 1024:.2f} MB"
    )
    if args.dry_run:
        print("（dry-run，未改写任何文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
