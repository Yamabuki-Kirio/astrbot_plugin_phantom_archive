import json
from pathlib import Path

from PIL import Image


def test_shipped_compositions_have_native_alpha_and_known_layers():
    root = Path(__file__).resolve().parents[1] / "data/assets"
    report = json.loads((root / "character_compositions.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert len(report["imported"]) == 63
    for row in report["imported"]:
        assert row["has_back"] is False
        entry = manifest["characters"][row["slug"]]
        assert entry["portrait"] == row["path"]
        assert entry["portraits"][0]["path"] == row["path"]
        assert row["path"].endswith("/portrait.webp")
        assert len(entry["layer_sources"]) == (2 if row["has_back"] else 1)
        with Image.open(root / row["path"]) as image:
            assert image.size == (720, 960)
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
