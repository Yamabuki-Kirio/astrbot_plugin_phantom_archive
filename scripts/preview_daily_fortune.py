"""使用正式运势对象和本地 Edge 输出卡片预览。"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from archive import image_templates, visual_context
from archive.asset_manifest import load_manifest
from archive.asset_resolver import AssetResolver
from archive.daily import models, presenter, tarot


def main():
    output = ROOT / "output/daily-redesign"
    output.mkdir(parents=True, exist_ok=True)
    resolver = AssetResolver(load_manifest(ROOT / "data/assets/manifest.json"))
    character = models.DailyCharacter(
        key="joker", slug="joker", name="雨宫莲", element="诅咒", position="反抗", rarity=5,
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 820, "height": 900}, device_scale_factor=1)
        for theme in ("dark", "light"):
            for grade, pool in tarot.GRADE_POOLS.items():
                name, meaning, reversed_position = pool[0]
                fortune = models.DailyFortune(
                    date_key="2026-11-12", character=character,
                    tarot=models.TarotDraw(name=name, meaning=meaning, is_reversed=reversed_position),
                )
                card = presenter.build_page(fortune)
                html = output / f"{theme}-{grade}.html"
                html.write_text(image_templates.render_page(
                    card, theme=theme, visual=visual_context.build(card, resolver=resolver),
                ), encoding="utf-8")
                page.goto(html.as_uri())
                page.evaluate("document.fonts.ready")
                page.locator(".hero-art").evaluate("image => image.decode()")
                page.locator(".card").screenshot(path=str(output / f"{theme}-{grade}.png"))
                assert page.locator(".fortune-grade").inner_text() == grade
                assert page.locator(".content").evaluate("content => content.scrollWidth <= content.clientWidth")
        browser.close()
    print(output)


if __name__ == "__main__":
    main()
