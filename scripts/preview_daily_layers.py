"""使用正式每日运势模板输出代表角色的本地验收截图。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from archive import cards, image_templates, visual_context
from archive.asset_manifest import load_manifest
from archive.asset_resolver import AssetResolver
from PIL import Image, ImageDraw

light = "--light" in sys.argv
output = ROOT / ("output/daily-layers-light" if light else "output/daily-layers")
output.mkdir(parents=True, exist_ok=True)
resolver = AssetResolver(load_manifest(ROOT / "data/assets/manifest.json"))
keys = [arg for arg in sys.argv[1:] if arg != "--light"] or ["leon", "j-c", "puppet-wavecatcher", "bui", "mont", "joker"]
for key in keys:
    page = cards.CardPage(category="每日运势", title=key, subtitle="今日角色 · 构图验收", lead="2026年11月8日",
        hero_kind="character", hero_key=key, sections=(
            cards.Section("塔罗指引", (cards.Block("item", ("· 星星 · 大吉", "希望与灵感正在靠近。", "宜：整理计划，主动交流。", "忌：急于求成。")),)),
            cards.Section("综合建议", (cards.Block("para", ("保持自己的节奏，把精力放在真正重要的事情上。",)),))))
    html = output / f"{key}.html"
    html.write_text(image_templates.render_page(page, theme="light" if light else "dark",
        visual=visual_context.build(page, resolver=resolver)), encoding="utf-8")
    subprocess.run([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "--headless", "--disable-gpu",
        "--no-sandbox", f"--user-data-dir={output / 'browser-profile'}", "--hide-scrollbars", "--window-size=820,680",
        f"--screenshot={output / (key + '.png')}", html.as_uri()], capture_output=True, timeout=30, check=True)
sheet = Image.new("RGB", (1230, ((len(keys)+2)//3)*340), "#20232c")
for i, key in enumerate(keys):
    image = Image.open(output / f"{key}.png").convert("RGB")
    image.thumbnail((410,340))
    sheet.paste(image, ((i%3)*410,(i//3)*340))
sheet.save(output / "contact.jpg")
print(output / "contact.jpg")
