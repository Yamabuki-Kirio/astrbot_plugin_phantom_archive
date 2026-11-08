"""导入已人工核对的人物前景映射；保留原生 alpha，绝不进行黑色抠图。

用法：python scripts/import_unpacked_portraits.py --mapping PATH
mapping 格式为 {"char:代号": {"front": "文件名", "source": ["原图路径", ...]}}。
仅处理与现有清单精确匹配的角色；不导入 back（人格面具）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def import_portraits(mapping_path: Path, assets: Path) -> dict:
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    manifest_path = assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    imported = []
    for key, record in mapping.items():
        slug = key.removeprefix("char:").lower().replace("·", "-").replace("&", "-")
        if slug not in manifest["characters"]:
            continue
        source = Path(record["source"][0])
        if source.name != record["front"]:
            raise ValueError(f"前景路径不一致: {key}")
        with Image.open(source) as original:
            image = original.convert("RGBA")
        alpha = image.getchannel("A")
        if alpha.getextrema()[0] == 255:
            raise ValueError(f"原图没有透明区域: {key}")
        box = alpha.getbbox()
        if box is None:
            raise ValueError(f"原图为空: {key}")
        image = image.crop(box)
        image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        relative = f"characters/{slug}/unpacked.webp"
        destination = assets / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", lossless=True, method=6)
        entry = {
            "portrait": relative,
            "portraits": [{"path": relative, "source_status": "community"}],
            "background": relative,
            "source_status": "community",
            "matched_by": "verified-unpacked-foreground",
            "form_inherited": False,
            "source_file": source.name,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "processing": "native-alpha; alpha-bounds crop; max-edge 2400; lossless WebP",
        }
        manifest["characters"][slug] = entry
        imported.append({"slug": slug, "source_file": source.name, "path": relative,
                         "size": list(image.size), "source_sha256": entry["source_sha256"]})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"imported": imported, "retained_original": sorted(set(manifest["characters"]) - {r["slug"] for r in imported}),
              "note": "仅人物前景；未匹配者保留原图；武器未修改；背景复用人物透明图，不使用人格面具。"}
    (assets / "unpacked_portraits.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def contact_sheet(report: dict, assets: Path, output: Path) -> None:
    rows = report["imported"]
    sheet = Image.new("RGB", (1000, ((len(rows) + 7) // 8) * 205), "#303440")
    draw = ImageDraw.Draw(sheet)
    for index, row in enumerate(rows):
        with Image.open(assets / row["path"]) as original:
            image = original.convert("RGBA")
        image.thumbnail((119, 175))
        x, y = (index % 8) * 125, (index // 8) * 205
        sheet.paste(image, (x + (125 - image.width) // 2, y), image)
        draw.text((x + 2, y + 178), row["slug"], fill="white")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=ROOT / "data/assets")
    parser.add_argument("--preview", type=Path, default=ROOT / "output/unpacked-portraits.jpg")
    args = parser.parse_args()
    report = import_portraits(args.mapping, args.assets)
    contact_sheet(report, args.assets, args.preview)
    print(json.dumps({"imported": len(report["imported"]), "retained_original": report["retained_original"]}, ensure_ascii=True))
