"""下载 Lufel 角色页实际使用的武器图并写入素材清单。

图片 URL 由角色页公开模板给出：
``/assets/img/character-weapon/<角色键>-<星级序号>.png``。
来源属于社区站点，清单固定标记为 ``community``，不冒充官方素材。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote
import requests

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "p5x_zh.sqlite3"
MANIFEST = ROOT / "data" / "assets" / "manifest.json"
BASE = "https://lufel.net/assets/img/character-weapon"
MAX_BYTES = 8 * 1024 * 1024


def suffix(weapon_key: str) -> str:
    # weapon5-1 -> 5-01；也兼容以后出现的 weapon4-2。
    star, index = weapon_key.removeprefix("weapon").split("-", 1)
    return f"{int(star)}-{int(index):02d}"


def fetch(url: str) -> bytes:
    response = requests.get(
        url,
        timeout=10,
        headers={"User-Agent": "phantom-archive-asset-fetcher/1.0"},
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    if not content_type.startswith("image/"):
        raise ValueError(f"非图片响应：{content_type}")
    data = response.content
    if len(data) > MAX_BYTES:
        raise ValueError("图片超过 8 MiB")
    return data


def save_webp(data: bytes, target: Path) -> None:
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGBA").save(target, "WEBP", quality=88, method=6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    weapons = manifest.setdefault("weapons", {})
    ok = missing = 0
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT character_key, weapon_key, name_zh FROM zh_weapons ORDER BY id"
        ).fetchall()
    def download(row):
        owner, weapon_key, name = row
        url = f"{BASE}/{quote(owner, safe='')}-{suffix(weapon_key)}.png"
        relative = f"weapons/{quote(owner, safe='')}/{weapon_key}.webp"
        target = MANIFEST.parent / relative
        if target.is_file() and target.stat().st_size:
            return row, url, relative, b"", None
        try:
            data = fetch(url)
            return row, url, relative, data, None
        except Exception as exc:
            return row, url, relative, b"", exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(download, rows))
    for row, url, relative, data, error in results:
        owner, weapon_key, name = row
        if error is None:
            if not args.dry_run and data:
                save_webp(data, MANIFEST.parent / relative)
            weapons[f"{owner}:{weapon_key}"] = {
                "image": relative,
                "source_url": url,
                "source_status": "community",
                "display_name": name,
            }
            ok += 1
        else:  # 单张缺失不应中止全量抓取
            missing += 1
            print(f"MISS {owner}:{weapon_key} {name}")
    if not args.dry_run:
        MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"武器图片：成功 {ok}/{len(rows)}，缺失 {missing}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
