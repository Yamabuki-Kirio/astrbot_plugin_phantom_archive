"""统计全量角色的技能加点推荐覆盖情况。

只读打开 p5x_zh.sqlite3，走与插件完全相同的解析路径，
输出「有推荐 / 无推荐 / 待核对」三类数量与名单，供 README 与验收报告引用。

用法：
    python scripts/verify_recommendation.py [--db 路径] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from archive import recommendation  # noqa: E402
from archive.service import CONFLICT_SCOPE_CHARACTER, ArchiveService  # noqa: E402

DEFAULT_DB = PLUGIN_ROOT / "data" / "p5x_zh.sqlite3"


def collect(service: ArchiveService) -> dict:
    rows = service._repo.list_characters()
    have: list[str] = []
    missing: list[str] = []
    review: list[str] = []
    samples: dict[str, dict] = {}

    for character in rows:
        profile = service._with_recommendation(
            service._profile(character, CONFLICT_SCOPE_CHARACTER)
        )
        item = profile.recommendation
        name = character.name_zh
        if not item.available:
            missing.append(name)
            continue
        have.append(name)
        if item.needs_review:
            review.append(name)
        if name not in samples:
            samples[name] = {
                "priority": item.priority_text,
                "order": item.order_text,
                "notes": list(item.notes),
                "raw": item.raw_priority,
            }

    return {
        "total": len(rows),
        "available": len(have),
        "missing": len(missing),
        "needs_review": len(review),
        "missing_names": missing,
        "needs_review_names": review,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="统计技能加点推荐覆盖率")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="p5x_zh.sqlite3 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    service = ArchiveService.open(str(db_path), db_path.parent)
    try:
        stats = collect(service)
    finally:
        service.close()

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    print("技能加点推荐覆盖")
    print("─" * 46)
    print(f"角色总数    {stats['total']} 名")
    print(f"有推荐      {stats['available']} 名")
    print(f"无推荐      {stats['missing']} 名（显示「{recommendation.UNAVAILABLE_TEXT}」）")
    print(f"待人工核对  {stats['needs_review']} 名")
    if stats["missing_names"]:
        print()
        print("无推荐名单：" + "、".join(stats["missing_names"]))
    if stats["needs_review_names"]:
        print("待核对名单：" + "、".join(stats["needs_review_names"]))
    print()
    print("样例：")
    for name, sample in list(stats["samples"].items())[:3]:
        print(f"  {name}")
        print(f"    优先级：{sample['priority']}")
        print(f"    推荐顺序：{sample['order']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
