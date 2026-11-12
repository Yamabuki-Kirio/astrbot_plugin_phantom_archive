"""随机抽取角色和塔罗，使用正式展示链生成浅色卡片及总览。"""

import json
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from archive import image_templates, visual_context
from archive.asset_manifest import load_manifest
from archive.asset_resolver import AssetResolver
from archive.daily import models, presenter, tarot


def main():
    output = ROOT / "output" / ("daily-random-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    manifest = json.loads((ROOT / "data/assets/manifest.json").read_text(encoding="utf-8"))
    resolver = AssetResolver(load_manifest(ROOT / "data/assets/manifest.json"))
    with sqlite3.connect((ROOT / "data/p5x_zh.sqlite3").as_uri() + "?mode=ro", uri=True) as database:
        rows = database.execute(
            "SELECT source_key, slug, name_zh, codename_zh, element_zh, position_zh, rarity FROM zh_characters"
        ).fetchall()
    candidates = [models.DailyCharacter(*row) for row in rows if row[1] in manifest["characters"]
                  and "complete" in manifest["characters"][row[1]].get("processing", "")]
    rng = random.Random()
    selected = rng.sample(candidates, min(12, len(candidates)))
    records = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 980, "height": 1000}, device_scale_factor=1)
        for index, character in enumerate(selected, 1):
            fortune = models.DailyFortune(
                date_key=datetime.now().strftime("%Y-%m-%d"), character=character, tarot=tarot.draw(rng),
            )
            card = presenter.build_page(fortune)
            stem = f"{index:02d}-{character.slug}-{fortune.grade}"
            html = output / f"{stem}.html"
            html.write_text(image_templates.render_page(
                card, theme="light", visual=visual_context.build(card, resolver=resolver),
            ), encoding="utf-8")
            page.goto(html.as_uri())
            page.evaluate("document.fonts.ready")
            page.locator(".hero-art").evaluate("image => image.decode()")
            page.locator(".card").screenshot(path=str(output / f"{stem}.png"))
            assert page.locator(".content").evaluate("content => content.scrollWidth <= content.clientWidth")
            records.append({"file": f"{stem}.png", "character": character.name, "grade": fortune.grade,
                            "tarot": fortune.tarot.name, "reversed": fortune.tarot.is_reversed})
        browser.close()
    sheet = Image.new("RGB", (1470, ((len(records) + 2) // 3) * 440), "#dfe3e9")
    draw = ImageDraw.Draw(sheet)
    for index, record in enumerate(records):
        with Image.open(output / record["file"]) as image:
            image.thumbnail((480, 410))
            left, top = index % 3 * 490, index // 3 * 440
            sheet.paste(image, (left, top))
            draw.text((left + 6, top + 416), f'{index + 1:02d}  {selected[index].slug}', fill="#222222")
    sheet.save(output / "contact.jpg", quality=92)
    (output / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    for record in records:
        print(record["file"], record["character"])


if __name__ == "__main__":
    main()
