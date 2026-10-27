"""从国服官网抓取角色立绘，按角色归档，供卡片随机取用。

数据源只有一个，且是官方站点：

    https://p5x.wanmei.com/js/roleData<日期>.js

这个文件里每个角色条目带若干张立绘（role1 / role2 / role3 / role4 …），
其中 role1 是默认立绘、role2 是切换形态后的立绘，role3/role4 是后续追加的形态。
官网**没有**给出这些图对应的形态名称，所以这里只做两件不做推断的事：

1. 名字完全匹配（含一张显式别名表，见 ALIASES）→ 该角色收下它的全部立绘；
2. 库里带「·」的形态角色 → 只有在官网条目能明确指到某一张图时才单独映射
   （见 FORMS，依据是立绘内容与形态名一致），否则沿用基础角色的图集并标记
   ``form_inherited``，绝不硬猜是哪一套。

来源状态一律记 ``official``，并保留每张图的原始 URL。

用法：

    python scripts/fetch_official_art.py --dry-run     # 只看映射结果
    python scripts/fetch_official_art.py               # 真下载
    python scripts/fetch_official_art.py --source-file roleData.js
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from PIL import Image  # noqa: E402

DEFAULT_DB = PLUGIN_ROOT / "data" / "p5x_zh.sqlite3"
ASSETS_DIR = PLUGIN_ROOT / "data" / "assets"
MANIFEST_PATH = ASSETS_DIR / "manifest.json"

SOURCE_URL = "https://p5x.wanmei.com/js/roleData260924.js"
SOURCE_ORIGIN = "https://p5x.wanmei.com"
OFFICIAL_STATUS = "official"

MAX_EDGE = 1080
BACKGROUND_SIZE = (1200, 900)

# 官网写法 → 库内写法。只做「同一个人的不同写法」，不跨角色。
ALIASES: dict[str, str] = {
    "主人公": "主角",
    "白鸟城司": "白鸟诚司",
    "滕川雪实": "藤川雪实",
    "琴音.蒙塔涅": "琴音·蒙塔涅",
    "李瑶玲": "李瑶铃",
    "Joker": "雨宫莲",
}

# 库内形态名 → (官网基础条目名, 该形态对应的 role 键)。
# 只登记**能确认**的：新井素羽 role3 是泳装（夏日），role4 是另一套（晴光），
# 其余形态官网只给了一张追加立绘，按 role3 对应唯一形态处理。
FORMS: dict[str, tuple[str, str]] = {
    "新井素羽·夏日": ("新井素羽", "role3"),
    "新井素羽·晴光": ("新井素羽", "role4"),
    "加纳骏·拓荒": ("加纳骏", "role3"),
    "多祢村理子·未央": ("多祢村理子", "role3"),
    "池波星辉·永夜": ("池波星辉", "role3"),
    "琴音 蒙塔涅·漫舞": ("琴音.蒙塔涅", "role3"),
    "李瑶铃·太平乐": ("李瑶玲", "role3"),
    "野毛朋子·夏日": ("野毛朋子", "role3"),
    "佐原海夕·夏日": ("佐原海夕", "role3"),
    "YUI·幻彩": ("YUI", "role3"),
    "宫下美波·夏日": ("宫下美波", "role3"),
    "鹿野莓·夏日": ("鹿野莓", "role3"),
}

ROLE_KEY_ORDER = ("role1", "role2", "role3", "role4", "role5", "role6")


@dataclass
class OfficialEntry:
    label: str
    images: dict[str, str] = field(default_factory=dict)

    def ordered(self) -> list[tuple[str, str]]:
        return [(key, self.images[key]) for key in ROLE_KEY_ORDER if self.images.get(key)]


def parse_role_data(source: str) -> list[OfficialEntry]:
    """解析官网 roleData 脚本。

    角色名只出现在条目前面的 ``// 注释`` 里（``name`` 字段是名字图片的 URL），
    所以按行扫：注释给名字，``roleN: '...'`` 给图，``id:`` 收尾。
    """
    entries: list[OfficialEntry] = []
    current: dict[str, str] = {}
    label: str | None = None

    for raw_line in source.splitlines():
        line = raw_line.strip()

        comment = re.match(r"^//\s*(\S.*)$", line)
        if comment:
            text = comment.group(1).strip()
            if not text.startswith(("http", "p5x", "立绘", "角色", "人格")):
                label = text
            continue

        field_match = re.match(r"^(role\d+|id):\s*'?([^',]*?)'?,?$", line)
        if field_match:
            current[field_match.group(1)] = field_match.group(2).strip()
            continue

        if line.startswith("}"):
            if current.get("id") and label:
                images = {
                    key: value
                    for key, value in current.items()
                    if key.startswith("role") and value.startswith("http")
                }
                entries.append(OfficialEntry(label=label, images=images))
            current, label = {}, None

    return entries


def build_mapping(entries: list[OfficialEntry], db_names: list[str]) -> dict:
    """官网条目 ↔ 库内角色名。返回 {库内名: 计划}。"""
    by_label = {entry.label: entry for entry in entries}
    plan: dict[str, dict] = {}
    used_forms: set[str] = set()

    for name in db_names:
        entry = by_label.get(name)
        matched_by = "exact"

        if entry is None:
            alias_target = ALIASES.get(name)
            if alias_target and alias_target in by_label:
                entry, matched_by = by_label[alias_target], "alias"
            else:
                # 反过来查：库内名可能是官网名的别名目标
                for official_name, local_name in ALIASES.items():
                    if local_name == name and official_name in by_label:
                        entry, matched_by = by_label[official_name], "alias"
                        break

        if entry is not None:
            plan[name] = {
                "entry": entry,
                "matched_by": matched_by,
                "images": entry.ordered(),
                "form_inherited": False,
            }
            continue

        form = FORMS.get(name)
        if form:
            base_label, role_key = form
            base = by_label.get(base_label)
            if base and base.images.get(role_key):
                plan[name] = {
                    "entry": base,
                    "matched_by": "form",
                    "images": [(role_key, base.images[role_key])],
                    "form_inherited": False,
                }
                used_forms.add(name)
                continue

        plan[name] = {"entry": None, "matched_by": "missing", "images": [], "form_inherited": False}

    # 形态没单独映射到的，沿用基础角色的图集，但明确标记，不当作已核对
    for name, item in plan.items():
        if item["matched_by"] != "missing" or "·" not in name:
            continue
        base_name = re.split(r"[· ]", name)[0]
        base_item = plan.get(base_name)
        if base_item and base_item["images"]:
            item.update(
                {
                    "entry": base_item["entry"],
                    "matched_by": "inherited",
                    "images": base_item["images"],
                    "form_inherited": True,
                }
            )
    return plan


def download(url: str, client, *, attempts: int = 2) -> bytes:
    """下载一张图。失败重试一次，仍然失败就交给调用方记一笔。"""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.content
        except Exception as exc:  # noqa: BLE001 - 重试后仍失败就抛给上层
            last = exc
    raise last if last is not None else RuntimeError("下载失败")


def to_webp(data: bytes, path: Path, max_edge: int = MAX_EDGE) -> tuple[int, int]:
    import io

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.mode else "RGB")
        longest = max(image.size)
        if longest > max_edge:
            scale = max_edge / longest
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.LANCZOS,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="WEBP", quality=88, method=5)
        return image.size


def make_background(portrait_path: Path, out_path: Path) -> None:
    from PIL import ImageFilter

    with Image.open(portrait_path) as image:
        image = image.convert("RGB")
        width, height = BACKGROUND_SIZE
        scale = max(width / image.width, height / image.height)
        resized = image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.LANCZOS
        )
        left = (resized.width - width) // 2
        top = int((resized.height - height) * 0.2)
        cropped = resized.crop((left, top, left + width, top + height))
        blurred = cropped.filter(ImageFilter.GaussianBlur(16))
        overlay = Image.new("RGB", (width, height), (10, 11, 15))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.blend(blurred, overlay, 0.4).save(
            out_path, format="WEBP", quality=82, method=5
        )


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        return {"characters": {}, "weapons": {}}
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"characters": {}, "weapons": {}}
    data.setdefault("characters", {})
    data.setdefault("weapons", {})
    return data


def save_manifest(data: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取官网角色立绘")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--source", default=SOURCE_URL, help="roleData 脚本地址")
    parser.add_argument("--source-file", default="", help="改用本地已下载的脚本")
    parser.add_argument("--dry-run", action="store_true", help="只打印映射，不下载")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 名角色（调试）")
    parser.add_argument(
        "--force",
        action="store_true",
        help="已存在的图片也重新下载（默认跳过，方便中断后接着跑）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    if args.source_file:
        source = Path(args.source_file).read_text(encoding="utf-8")
        source_label = f"本地文件 {args.source_file}"
    else:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=60) as client:
            source = client.get(args.source).text
        source_label = args.source

    entries = parse_role_data(source)
    if not entries:
        print("没有解析到任何角色条目，请检查 roleData 脚本格式。", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        "select source_key, slug, name_zh from zh_characters order by source_key"
    ).fetchall()
    conn.close()

    db_names = [row[2] for row in rows]
    plan = build_mapping(entries, db_names)

    stats = {"exact": 0, "alias": 0, "form": 0, "inherited": 0, "missing": 0}
    for item in plan.values():
        stats[item["matched_by"]] += 1

    print("官网角色抓取计划")
    print("─" * 56)
    print(f"数据源      {source_label}")
    print(f"官网条目    {len(entries)} 个")
    print(f"库内角色    {len(db_names)} 名")
    print(
        f"名字完全匹配 {stats['exact']}｜别名 {stats['alias']}｜形态单图 {stats['form']}"
        f"｜形态沿用 {stats['inherited']}｜无图 {stats['missing']}"
    )
    total_images = sum(len(item["images"]) for item in plan.values() if not item["form_inherited"])
    print(f"可下载图片  {total_images} 张（同一角色多张图会全部收下）")

    missing = [name for name, item in plan.items() if item["matched_by"] == "missing"]
    if missing:
        print()
        print("官网没有的角色（将使用占位图）：" + "、".join(missing))

    inherited = [name for name, item in plan.items() if item["form_inherited"]]
    if inherited:
        print()
        print("形态未区分、沿用基础角色图集：" + "、".join(inherited))

    if args.dry_run:
        print()
        print("（--dry-run，不下载）")
        for name, item in list(plan.items())[:5]:
            if item["images"]:
                print(f"  {name} ← {len(item['images'])} 张：{item['images'][0][1].split('/')[-1]}")
        return 0

    import httpx

    manifest = load_manifest()
    manifest.setdefault(
        "_comment", "由 scripts/build_asset_manifest.py 与 fetch_official_art.py 共同维护"
    )
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written = skipped = failed = 0
    processed = 0

    # 连接 / 读取分开设超时：官网图有大到 5 MB 的，读超时给足；
    # 但连接卡住要快速失败，否则一张图能把整轮抓取拖死。
    timeout = httpx.Timeout(connect=15.0, read=60.0, write=20.0, pool=20.0)
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        for name, item in plan.items():
            if not item["images"]:
                continue
            if args.limit and processed >= args.limit:
                break
            processed += 1

            slug = next(
                (row[1] or row[0] for row in rows if row[2] == name), None
            ) or name
            folder = ASSETS_DIR / "characters" / slug
            portraits = []
            for index, (role_key, url) in enumerate(item["images"], start=1):
                target = folder / f"{index}.webp"
                if target.is_file() and target.stat().st_size > 0 and not args.force:
                    skipped += 1
                else:
                    try:
                        data = download(url, client)
                        to_webp(data, target)
                        written += 1
                    except Exception as exc:  # noqa: BLE001 - 单张失败不影响其他角色
                        failed += 1
                        print(f"  ! {name} {role_key} 下载失败：{type(exc).__name__}")
                        continue
                portraits.append(
                    {
                        "path": target.relative_to(ASSETS_DIR).as_posix(),
                        "source_url": url,
                        "source_status": OFFICIAL_STATUS,
                        "role_key": role_key,
                        "fetched_at": fetched_at,
                    }
                )

            if not portraits:
                continue

            background = folder / "background.webp"
            if not background.is_file() or args.force:
                try:
                    make_background(folder / "1.webp", background)
                except Exception:  # noqa: BLE001
                    background = None

            manifest["characters"][slug] = {
                "portrait": portraits[0]["path"],
                "portraits": portraits,
                "background": (
                    background.relative_to(ASSETS_DIR).as_posix() if background else None
                ),
                "source_url": portraits[0]["source_url"],
                "source_status": OFFICIAL_STATUS,
                "matched_by": item["matched_by"],
                "form_inherited": item["form_inherited"],
                "fetched_at": fetched_at,
            }

    # 官网没有的角色要显式登记成「有映射、无文件、来源不明」，
    # 不能留着上一版生成脚本写下的旧条目 —— 那些条目指向已删除的文件，
    # 校验时会既报「状态非法」又报「文件不存在」，看不出到底是谁的问题。
    for name, item in plan.items():
        if item["matched_by"] != "missing":
            continue
        slug = next((row[1] or row[0] for row in rows if row[2] == name), None) or name
        manifest["characters"][slug] = {
            "portrait": None,
            "portraits": [],
            "background": None,
            "source_url": None,
            "source_status": "unavailable",
            "matched_by": "missing",
            "form_inherited": False,
            "fetched_at": fetched_at,
        }

    save_manifest(manifest)
    print()
    print(
        f"新下载 {written} 张，跳过已存在 {skipped} 张，失败 {failed} 张，"
        f"清单：{MANIFEST_PATH}"
    )
    print("注意：立绘版权归游戏发行方，仅限个人自用，仓库不提交这些图片。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
