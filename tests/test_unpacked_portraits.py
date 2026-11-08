"""本机解包导入必须保留暗色人物像素，且不再轮换旧抠图。"""
import importlib.util
import json
from pathlib import Path

from PIL import Image


def test_import_native_alpha_foreground_only(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/import_unpacked_portraits.py"
    spec = importlib.util.spec_from_file_location("import_unpacked", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "front.png"
    image = Image.new("RGBA", (20, 20))
    for x in range(4, 16):
        for y in range(3, 18):
            image.putpixel((x, y), (0, 0, 0, 255))
    image.save(source)
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"char:J&C": {"front": source.name,
        "source": [str(source), "nonexistent-persona.png"]}}))
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text(json.dumps({"characters": {
        "j-c": {"portrait": "old.webp", "portraits": [{"path": "old.webp"}]},
        "unmatched": {"portrait": "keep.webp"}}, "weapons": {"test": {"image": "keep.webp"}}}))
    report = module.import_portraits(mapping, assets)
    result = json.loads((assets / "manifest.json").read_text())
    entry = result["characters"]["j-c"]
    assert entry["background"] == entry["portrait"]
    assert [p["path"] for p in entry["portraits"]] == [entry["portrait"]]
    with Image.open(assets / entry["portrait"]) as imported:
        assert imported.size == (12, 15)
        assert imported.convert("RGBA").getpixel((5, 5)) == (0, 0, 0, 255)
    assert report["retained_original"] == ["unmatched"]
    assert result["weapons"] == {"test": {"image": "keep.webp"}}
