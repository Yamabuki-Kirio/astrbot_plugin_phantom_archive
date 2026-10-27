"""生成三张本地占位图：角色 / 武器 / 档案。

占位图必须由本仓库自己生成，不引用任何第三方图片 ——
它们的用途正是「在没有任何素材时，卡片仍然是完整的」。

用法：
    python scripts/make_placeholders.py [--out data/assets/fallback]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]

SIZE = (720, 720)
BACKGROUND = (16, 18, 24)
ACCENT = (226, 58, 69)
INK = (232, 234, 239)
MUTED = (110, 116, 128)


def _base(color: tuple[int, int, int]) -> Image.Image:
    """深色渐变底 + 斜向扫描线，和卡片的档案风格保持一致。"""
    image = Image.new("RGB", SIZE, BACKGROUND)
    draw = ImageDraw.Draw(image)

    for y in range(SIZE[1]):
        ratio = y / (SIZE[1] - 1)
        shade = tuple(
            int(BACKGROUND[i] + (color[i] - BACKGROUND[i]) * (0.16 * (1 - ratio)))
            for i in range(3)
        )
        draw.line([(0, y), (SIZE[0], y)], fill=shade)

    for offset in range(-SIZE[1], SIZE[0], 26):
        draw.line(
            [(offset, SIZE[1]), (offset + SIZE[1], 0)],
            fill=(26, 29, 37),
            width=1,
        )

    draw.rectangle([0, 0, SIZE[0] - 1, SIZE[1] - 1], outline=(44, 49, 60), width=2)
    draw.rectangle([0, 0, SIZE[0] - 1, 5], fill=color)
    return image


def _character(color: tuple[int, int, int]) -> Image.Image:
    image = _base(color)
    draw = ImageDraw.Draw(image)
    cx = SIZE[0] // 2
    # 头 + 肩的剪影
    draw.ellipse([cx - 78, 186, cx + 78, 342], fill=(52, 57, 70))
    draw.ellipse([cx - 78, 186, cx + 78, 342], outline=MUTED, width=3)
    draw.pieslice([cx - 186, 372, cx + 186, 744], 180, 360, fill=(52, 57, 70))
    draw.arc([cx - 186, 372, cx + 186, 744], 180, 360, fill=MUTED, width=3)
    return image


def _weapon(color: tuple[int, int, int]) -> Image.Image:
    image = _base(color)
    draw = ImageDraw.Draw(image)
    cx = SIZE[0] // 2
    # 斜置的剑：剑身 + 护手 + 柄
    draw.polygon(
        [(cx + 150, 150), (cx + 186, 186), (cx - 96, 468), (cx - 132, 432)],
        fill=(60, 66, 80),
        outline=MUTED,
    )
    draw.line([(cx - 168, 372), (cx - 24, 516)], fill=color, width=12)
    draw.line([(cx - 126, 558), (cx - 42, 474)], fill=(70, 76, 90), width=22)
    draw.ellipse([cx - 158, 526, cx - 118, 566], outline=color, width=4)
    return image


def _archive(color: tuple[int, int, int]) -> Image.Image:
    image = _base(color)
    draw = ImageDraw.Draw(image)
    cx = SIZE[0] // 2
    # 一叠档案卡
    for index, shift in enumerate((42, 20, 0)):
        left = cx - 150 + shift
        top = 210 + shift
        draw.rounded_rectangle(
            [left, top, left + 300, top + 300],
            radius=10,
            fill=(34 + index * 8, 38 + index * 8, 48 + index * 8),
            outline=(70, 76, 90),
            width=2,
        )
    for line in range(5):
        y = 300 + line * 34
        width = 210 if line % 2 == 0 else 150
        draw.line([(cx - 110, y), (cx - 110 + width, y)], fill=MUTED, width=5)
    draw.line([(cx - 110, 300 - 34), (cx + 20, 300 - 34)], fill=color, width=8)
    return image


BUILDERS = {
    "character.webp": (_character, ACCENT),
    "weapon.webp": (_weapon, (216, 161, 58)),
    "archive.webp": (_archive, (67, 181, 129)),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成卡片占位图")
    parser.add_argument(
        "--out",
        default=str(REPO / "data" / "assets" / "fallback"),
        help="输出目录",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for name, (builder, color) in BUILDERS.items():
        image = builder(color)
        path = out / name
        image.save(path, format="WEBP", quality=88, method=6)
        print(f"{path.relative_to(REPO) if path.is_relative_to(REPO) else path}  {path.stat().st_size // 1024} KB")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
