"""按已核对的前后景映射导入配套人格面具，不修改人物原图。"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def run(source: Path, mapping_path: Path):
    manifest_path = ROOT / "data/assets/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    by_front = {item["front"]: item for item in mapping.values() if item.get("back")}
    imported, missing = [], []
    for slug, entry in manifest["characters"].items():
        front = entry.get("source_file", "")
        pair = by_front.get(front)
        if not pair or not (source / pair["back"]).is_file():
            missing.append(slug)
            continue
        original = source / pair["back"]
        with Image.open(original) as image:
            image = image.convert("RGBA")
        alpha = image.getchannel("A")
        bounds = alpha.getbbox()
        if not bounds or alpha.getextrema()[0] == 255:
            raise ValueError(f"人格面具透明通道异常: {original.name}")
        image = image.crop(bounds)
        image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        relative = f"characters/{slug}/persona.webp"
        destination = manifest_path.parent / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", quality=90, alpha_quality=100, method=6)
        entry["persona"] = {
            "path": relative, "source_status": "community",
            "source_file": original.name,
            "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "paired_front": front,
            "processing": "native alpha; complete alpha bounds; max edge 1800; WebP q90 alpha100",
        }
        imported.append({"slug": slug, "front": front, "back": original.name, "size": list(image.size)})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"imported": imported, "missing": missing}
    (manifest_path.parent / "persona_import.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("mapping", type=Path)
    args = parser.parse_args()
    run(args.source, args.mapping)
