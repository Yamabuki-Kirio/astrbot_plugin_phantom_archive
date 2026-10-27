"""校验素材清单与文件，输出真实覆盖率。

检查项（任一不过就返回非 0）：
1. 清单是合法 JSON，顶层有 characters / weapons；
2. 所有路径都是相对路径、且落在素材目录内；
3. 引用的文件真实存在、文件头是 PNG/JPEG/WebP、Pillow 能完整解码；
4. 单张源图不超过 8 MiB；
5. ``source_status`` 只能是 official / community / unavailable；
6. 写了 ``source_url`` 就不能同时标 unavailable；
7. 三张占位图必须存在。

用法：python scripts/verify_assets.py [--assets data/assets]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
VALID_STATUS = ("official", "community", "unavailable")
FALLBACKS = ("character.webp", "weapon.webp", "archive.webp")
PATH_FIELDS = ("portrait", "background", "image")


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


class Checker:
    def __init__(self, assets: Path):
        self.assets = assets
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.files = 0

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def check_file(self, relative: str, owner: str) -> bool:
        if not relative:
            return False
        if "://" in relative:
            self.error(f"{owner}: 清单里出现 URL，不允许热链：{relative}")
            return False
        path = Path(relative)
        if path.is_absolute():
            self.error(f"{owner}: 使用绝对路径：{relative}")
            return False
        full = (self.assets / path).resolve()
        try:
            full.relative_to(self.assets.resolve())
        except ValueError:
            self.error(f"{owner}: 路径越界：{relative}")
            return False
        if not full.is_file():
            self.error(f"{owner}: 文件不存在：{relative}")
            return False

        size = full.stat().st_size
        if size > MAX_BYTES:
            self.error(f"{owner}: 文件超过 8 MiB（{size // 1024} KB）")
            return False
        data = full.read_bytes()
        if sniff(data) is None:
            self.error(f"{owner}: 不是 PNG/JPEG/WebP：{relative}")
            return False
        try:
            previous = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = MAX_PIXELS
            try:
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    if image.size[0] * image.size[1] > MAX_PIXELS:
                        self.error(f"{owner}: 像素数超限：{relative}")
                        return False
            finally:
                Image.MAX_IMAGE_PIXELS = previous
        except Exception as exc:  # noqa: BLE001
            self.error(f"{owner}: 图片解码失败（{type(exc).__name__}）：{relative}")
            return False

        self.files += 1
        return True

    def check_entry(self, entry: dict, owner: str) -> None:
        status = entry.get("source_status")
        if status not in VALID_STATUS:
            self.error(f"{owner}: source_status 非法（{status!r}）")
        url = entry.get("source_url")
        if url and status == "unavailable":
            self.error(f"{owner}: 有 source_url 却标成 unavailable")
        if url and not str(url).startswith(("http://", "https://")):
            self.warn(f"{owner}: source_url 不是 http(s) 地址：{url}")
        if status == "official" and url and "wanmei.com" not in str(url):
            self.warn(f"{owner}: 标为 official 但来源不是 p5x.wanmei.com：{url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验素材清单")
    parser.add_argument("--assets", default=str(REPO / "data" / "assets"))
    args = parser.parse_args()

    assets = Path(args.assets)
    manifest_path = assets / "manifest.json"
    checker = Checker(assets)

    if not manifest_path.is_file():
        print(f"× 清单不存在：{manifest_path}")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"× 清单不是合法 JSON：{exc}")
        return 1

    characters = manifest.get("characters")
    weapons = manifest.get("weapons")
    if not isinstance(characters, dict) or not isinstance(weapons, dict):
        print("× 清单缺少 characters / weapons")
        return 1

    for name in FALLBACKS:
        path = assets / "fallback" / name
        if not path.is_file():
            checker.error(f"占位图缺失：fallback/{name}")
        else:
            checker.check_file(f"fallback/{name}", "fallback")

    char_with_portrait = 0
    char_with_source = 0
    portrait_files = 0
    matched_by: dict[str, int] = {}
    inherited: list[str] = []
    for key, entry in characters.items():
        if not isinstance(entry, dict):
            checker.error(f"角色 {key}: 条目不是对象")
            continue
        checker.check_entry(entry, f"角色 {key}")
        if checker.check_file(entry.get("portrait") or "", f"角色 {key}"):
            char_with_portrait += 1
            if entry.get("source_status") in ("official", "community"):
                char_with_source += 1
        if entry.get("background"):
            checker.check_file(entry["background"], f"角色 {key} 背景")

        label = entry.get("matched_by") or "unknown"
        matched_by[label] = matched_by.get(label, 0) + 1
        if entry.get("form_inherited"):
            inherited.append(key)

        # 同一角色的全部立绘都要能打开，否则「多图随机取用」会取到坏图
        portraits = entry.get("portraits")
        if isinstance(portraits, list):
            for index, item in enumerate(portraits, start=1):
                if not isinstance(item, dict):
                    checker.error(f"角色 {key}: portraits[{index}] 不是对象")
                    continue
                checker.check_entry(item, f"角色 {key} 立绘 {index}")
                if checker.check_file(
                    item.get("path") or "", f"角色 {key} 立绘 {index}"
                ):
                    portrait_files += 1

    weapon_with_image = 0
    weapon_with_source = 0
    for key, entry in weapons.items():
        if not isinstance(entry, dict):
            checker.error(f"武器 {key}: 条目不是对象")
            continue
        checker.check_entry(entry, f"武器 {key}")
        if checker.check_file(entry.get("image") or "", f"武器 {key}"):
            weapon_with_image += 1
            if entry.get("source_status") in ("official", "community"):
                weapon_with_source += 1

    total_char = len(characters)
    total_weapon = len(weapons)
    print("素材校验结果")
    print("─" * 46)
    print(f"角色映射 {total_char:>4} 项｜有立绘 {char_with_portrait:>4} 项"
          f"｜已登记来源 {char_with_source:>4} 项")
    print(f"立绘文件 {portrait_files:>4} 张")
    print(f"武器映射 {total_weapon:>4} 项｜有图片 {weapon_with_image:>4} 项"
          f"｜已登记来源 {weapon_with_source:>4} 项")
    print(f"已校验文件 {checker.files} 个")

    if matched_by:
        order = ("exact", "alias", "form", "inherited", "missing", "unknown")
        parts = [
            f"{label} {matched_by[label]}"
            for label in order
            if matched_by.get(label)
        ]
        print(f"匹配方式 {'｜'.join(parts)}")
    if inherited:
        print(f"形态沿用基础图集 {len(inherited)} 名：{'、'.join(inherited)}")

    def pct(part: int, whole: int) -> str:
        return f"{part / whole * 100:.1f}%" if whole else "n/a"

    print()
    print(f"角色立绘覆盖率：{char_with_portrait}/{total_char} = {pct(char_with_portrait, total_char)}")
    print(f"武器图片覆盖率：{weapon_with_image}/{total_weapon} = {pct(weapon_with_image, total_weapon)}")
    if char_with_portrait:
        print(f"人均立绘张数：{portrait_files / char_with_portrait:.2f} 张")

    if checker.warnings:
        print()
        print(f"警告 {len(checker.warnings)} 条：")
        for message in checker.warnings[:20]:
            print(f"  ! {message}")
    if checker.errors:
        print()
        print(f"错误 {len(checker.errors)} 条：")
        for message in checker.errors[:30]:
            print(f"  × {message}")
        return 1

    print()
    print("✓ 清单与文件校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
