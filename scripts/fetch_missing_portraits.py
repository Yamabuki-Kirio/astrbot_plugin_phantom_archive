"""补齐天田乾、荒垣真次郎：使用 ATLUS P3R 官网人物立绘，明确记录原作品。

仅更新这两个角色，不重新生成其他角色或武器的素材清单。
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data/assets"
SOURCES = {
    "아마다": ("天田乾", "img_character9_8c95abfc3076bd8cf07c14397744ddf5.webp"),
    "아라가키": ("荒垣真次郎", "img_character10_62be914bf13722f20f8bb1cb0512d50f.webp"),
}


def main():
    manifest_path = ASSETS / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key, (name, filename) in SOURCES.items():
        url = f"https://p3re.jp/resources/img/top/character_modal/{filename}"
        with urlopen(url, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError(f"{name}: image exceeds 8 MiB")
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            image = image.convert("RGBA")
            bbox = image.getchannel("A").getbbox()
            if bbox is None:
                raise ValueError(f"{name}: empty image")
            # 官网的方形画布含大块透明留白；裁到人物后保持全身等比缩放。
            image = image.crop(bbox)
            image.thumbnail((672, 912), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (720, 960))
            canvas.alpha_composite(image, ((720 - image.width) // 2, (960 - image.height) // 2))
            relative = f"characters/{key}/portrait.webp"
            target = ASSETS / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(target, "WEBP", quality=92, alpha_quality=100, method=6)
        manifest["characters"][key] = {
            "portrait": relative,
            "portraits": [{"path": relative, "source_status": "official", "source_url": url}],
            "background": relative,
            "source_status": "official",
            "source_url": url,
            "source_page": "https://p3re.jp/",
            "source_title": "Persona 3 Reload / ペルソナ３ リロード",
            "source_note": "P3R official character artwork; not P5X-specific artwork",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "matched_by": "verified-official-character",
            "form_inherited": False,
            "processing": "native alpha; trim transparent margins; fit 720x960; WebP q92 alpha100",
        }
        print(f"{name}: {relative}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
