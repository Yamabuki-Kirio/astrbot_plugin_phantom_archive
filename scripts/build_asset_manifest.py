"""扫描素材目录，生成 ``data/assets/manifest.json`` 与覆盖率报告。

只做两件事：**登记**和**统计**。不做任何「猜哪张图是哪个角色」的事 ——
文件名必须和数据库里的标识符完全对上，对不上就列进未匹配清单。

用法：
    python scripts/build_asset_manifest.py \
        --assets data/assets \
        --db data/p5x_zh.sqlite3 \
        --sources assets_sources.json \
        --report data/assets/coverage.json

``--sources`` 是可选的来源登记表，形如::

    {"characters/kotone/portrait.webp": {"source_url": "https://…", "source_status": "community"}}

没有登记来源的文件仍然会写进清单（图能用），但来源状态记 ``unavailable``，
并在报告的「未登记来源」里列出来 —— 绝不默认标成 official。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

IMAGE_SUFFIXES = (".webp", ".png", ".jpg", ".jpeg")
PORTRAIT_NAMES = ("portrait", "character", "art")
BACKGROUND_NAMES = ("background", "bg")
VALID_STATUS = ("official", "community", "unavailable")


def open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def find_file(directory: Path, stems: tuple[str, ...]) -> Path | None:
    if not directory.is_dir():
        return None
    for stem in stems:
        for suffix in IMAGE_SUFFIXES:
            candidate = directory / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def load_sources(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"！来源登记表读取失败（{type(exc).__name__}），全部按未登记处理")
        return {}
    return data if isinstance(data, dict) else {}


def source_for(sources: dict, relative: str) -> tuple[str | None, str]:
    record = sources.get(relative) or sources.get(Path(relative).name) or {}
    if not isinstance(record, dict):
        return None, "unavailable"
    url = record.get("source_url")
    status = record.get("source_status")
    if not isinstance(status, str) or status.lower() not in VALID_STATUS:
        status = "unavailable"
    return (url if isinstance(url, str) and url.strip() else None), status.lower()


def build(db: Path, assets: Path, sources: dict) -> tuple[dict, dict]:
    connection = open_readonly(db)
    try:
        characters = connection.execute(
            "SELECT source_key, slug, name_zh FROM zh_characters ORDER BY source_key"
        ).fetchall()
        weapons = connection.execute(
            "SELECT character_key, weapon_key, name_zh FROM zh_weapons ORDER BY character_key, weapon_key"
        ).fetchall()
    finally:
        connection.close()

    character_entries: dict[str, dict] = {}
    weapon_entries: dict[str, dict] = {}
    unmatched_files: list[str] = []
    used_files: set[str] = set()

    for row in characters:
        key = (row["slug"] or "").strip() or row["source_key"]
        folder = assets / "characters" / key
        portrait = find_file(folder, PORTRAIT_NAMES)
        background = find_file(folder, BACKGROUND_NAMES)

        entry = {"portrait": None, "background": None, "source_url": None, "source_status": "unavailable"}
        for field, found in (("portrait", portrait), ("background", background)):
            if found is None:
                continue
            relative = found.relative_to(assets).as_posix()
            used_files.add(relative)
            url, status = source_for(sources, relative)
            entry[field] = relative
            if field == "portrait":
                entry["source_url"] = url
                entry["source_status"] = status
        character_entries[key] = entry

    for row in weapons:
        owner = (row["character_key"] or "").strip()
        weapon_key = (row["weapon_key"] or "").strip()
        if not owner or not weapon_key:
            continue
        folder = assets / "weapons" / owner
        found = find_file(folder, (weapon_key,))
        entry = {"image": None, "source_url": None, "source_status": "unavailable"}
        if found is not None:
            relative = found.relative_to(assets).as_posix()
            used_files.add(relative)
            url, status = source_for(sources, relative)
            entry.update({"image": relative, "source_url": url, "source_status": status})
        weapon_entries[f"{owner}:{weapon_key}"] = entry

    # 目录里有、但数据库里对不上的文件，单独列出来，绝不硬塞进清单
    for folder, pattern in (("characters", "*/*"), ("weapons", "*/*")):
        base = assets / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            relative = path.relative_to(assets).as_posix()
            if relative not in used_files:
                unmatched_files.append(relative)

    manifest = {
        "_comment": "由 scripts/build_asset_manifest.py 生成；路径相对本文件所在目录",
        "characters": character_entries,
        "weapons": weapon_entries,
    }

    report = {
        "generated_by": "scripts/build_asset_manifest.py",
        "characters": {
            "total": len(character_entries),
            "with_portrait": sum(1 for item in character_entries.values() if item["portrait"]),
            "with_background": sum(1 for item in character_entries.values() if item["background"]),
            "with_source": sum(
                1
                for item in character_entries.values()
                if item["portrait"] and item["source_status"] != "unavailable"
            ),
            "missing": sorted(
                key for key, item in character_entries.items() if not item["portrait"]
            ),
        },
        "weapons": {
            "total": len(weapon_entries),
            "with_image": sum(1 for item in weapon_entries.values() if item["image"]),
            "with_source": sum(
                1
                for item in weapon_entries.values()
                if item["image"] and item["source_status"] != "unavailable"
            ),
            "missing": sorted(
                key for key, item in weapon_entries.items() if not item["image"]
            ),
        },
        "unregistered_source": sorted(
            relative
            for item in list(character_entries.values()) + list(weapon_entries.values())
            for relative in (item.get("portrait"), item.get("background"), item.get("image"))
            if relative and item.get("source_status") == "unavailable"
        ),
        "unmatched_files": unmatched_files,
    }
    return manifest, report


def main() -> int:
    parser = argparse.ArgumentParser(description="生成素材清单与覆盖率报告")
    parser.add_argument("--db", default=str(REPO / "data" / "p5x_zh.sqlite3"))
    parser.add_argument("--assets", default=str(REPO / "data" / "assets"))
    parser.add_argument("--sources", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    assets = Path(args.assets)
    manifest, report = build(
        Path(args.db),
        assets,
        load_sources(Path(args.sources) if args.sources else None),
    )

    out = Path(args.out) if args.out else assets / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = Path(args.report) if args.report else assets / "coverage.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    characters = report["characters"]
    weapons = report["weapons"]
    print(f"清单已写入：{out}")
    print(f"覆盖率报告：{report_path}")
    print()
    print(f"角色 {characters['total']} 名，有立绘 {characters['with_portrait']}，"
          f"有背景 {characters['with_background']}，已登记来源 {characters['with_source']}")
    print(f"武器 {weapons['total']} 把，有图 {weapons['with_image']}，"
          f"已登记来源 {weapons['with_source']}")
    if report["unregistered_source"]:
        print(f"！{len(report['unregistered_source'])} 个文件未登记来源（按 unavailable 记录）")
    if report["unmatched_files"]:
        print(f"！{len(report['unmatched_files'])} 个文件在数据库里找不到对应项：")
        for relative in report["unmatched_files"][:20]:
            print(f"    {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
