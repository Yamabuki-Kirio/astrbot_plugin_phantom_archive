"""从已核对的原始人物映射导入完整透明 WebP，禁止固定画布裁切。"""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def rebuild(source, apply=False):
    assets = ROOT / "data/assets"
    manifest_path = assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output = ROOT / "output/full-portraits"
    output.mkdir(parents=True, exist_ok=True)
    selected = []
    retained = []
    for slug, entry in manifest["characters"].items():
        filename = entry.get("source_file", "")
        original_path = source / filename
        if not filename or not original_path.is_file():
            retained.append(slug)
            continue
        if "persona" in filename.lower():
            raise ValueError(f"人格面具不能作为人物导入: {filename}")
        with Image.open(original_path) as original:
            image = original.convert("RGBA")
        alpha = image.getchannel("A")
        bounds = alpha.getbbox()
        if bounds is None or alpha.getextrema()[0] == 255:
            raise ValueError(f"无有效透明人物边界: {filename}")
        image = image.crop(bounds)
        image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        selected.append((slug, filename, image, original_path))
    for start in range(0, len(selected), 24):
        batch = selected[start:start + 24]
        sheet = Image.new("RGB", (1200, ((len(batch) + 5) // 6) * 250), "#cbd0d8")
        draw = ImageDraw.Draw(sheet)
        for index, (slug, filename, image, original_path) in enumerate(batch):
            thumb = image.copy()
            thumb.thumbnail((190, 210))
            left, top = index % 6 * 200, index // 6 * 250
            sheet.paste(thumb, (left + (200 - thumb.width) // 2, top), thumb)
            draw.text((left + 4, top + 212), slug, fill="black")
            draw.text((left + 4, top + 228), filename[:28], fill="black")
        sheet.save(output / f"review-{start // 24 + 1}.jpg")
    rows = []
    for slug, filename, image, original_path in selected:
        relative = f"characters/{slug}/portrait.webp"
        destination = assets / relative
        before = destination.stat().st_size if destination.exists() else 0
        if apply:
            image.save(destination, "WEBP", quality=88, alpha_quality=100, method=6)
            manifest["characters"][slug].update(
                portrait=relative, portraits=[{"path": relative, "source_status": "community"}],
                background=relative, source_file=filename,
                source_sha256=hashlib.sha256(original_path.read_bytes()).hexdigest(),
                processing="native alpha; complete alpha bounds; max edge 1800; WebP q88 alpha100; no canvas clipping",
                layer_sources=[filename],
            )
        rows.append({"slug": slug, "source": filename, "size": list(image.size),
                     "original_bytes": original_path.stat().st_size, "previous_webp_bytes": before,
                     "webp_bytes": destination.stat().st_size if apply else None})
    if apply:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"applied": apply, "imported": rows, "retained": retained}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"selected": len(rows), "retained": retained, "applied": apply}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rebuild(args.source, args.apply)
