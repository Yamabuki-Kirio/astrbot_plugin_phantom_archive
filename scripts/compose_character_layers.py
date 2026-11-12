"""将已核对的人物前景排为统一右侧构图，保留透明通道，不加入专属后景。"""
import argparse
import json
from pathlib import Path

from PIL import Image

from import_unpacked_portraits import ROOT, contact_sheet

# 特殊构图：双人、坐姿、横向动作不套用普通站姿放大倍率。
HEIGHTS = {"j-c": 1.12, "puppet": 1.2, "puppet-wavecatcher": 1.08,
           "marian-beachflower": 1.12, "chord-summer": 1.15,
           "navi": 1.15, "bui-cosmic": 1.2, "moko-seaside": 1.22,
           "mont": 1.3, "yukari": 1.3, "violet": 1.35}
CENTERS = {"puppet-wavecatcher": 650, "skull": 310, "violet": 390}


def load(path):
    with Image.open(path) as original:
        image = original.convert("RGBA")
    box = image.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    return image.crop(box) if box else image


def place(canvas, image, height, center, top):
    scale = height / image.height
    image = image.resize((round(image.width * scale), round(height)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(image, (round(center - image.width / 2), round(top)))


def compose(mapping_path, assets):
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    manifest_path = assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = []
    for key, record in mapping.items():
        slug = key.removeprefix("char:").lower().replace("·", "-").replace("&", "-")
        if slug not in manifest["characters"]:
            continue
        foreground = load(record["source"][0])
        foreground.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        canvas = foreground
        relative = f"characters/{slug}/portrait.webp"
        canvas.save(assets / relative, "WEBP", quality=88, method=6, alpha_quality=100)
        entry = manifest["characters"][slug]
        entry.update(portrait=relative, portraits=[{"path": relative, "source_status": "community"}],
                     background=relative, layer_sources=[Path(record["source"][0]).name],
                     processing="native-alpha; complete foreground bounds; max edge 1800; WebP q88 alpha100")
        rows.append({"slug": slug, "path": relative, "size": list(canvas.size),
                     "has_back": False})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"imported": rows}
    (assets / "character_compositions.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contact_sheet(report, assets, ROOT / "output/layered-portraits.jpg")
    print(f"Composed {len(rows)} characters")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=ROOT / "data/assets")
    args = parser.parse_args()
    compose(args.mapping, args.assets)
