"""以真实数据库、正式分页及模板生成三背景验收图。"""
import json
import random
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from archive import image_templates, visual_context
from archive.asset_manifest import load_manifest
from archive.asset_resolver import AssetResolver
from archive.backgrounds import BACKGROUND_SIZES
from archive.daily import models, presenter, tarot
from archive.service import ArchiveService
from archive.identity import RequesterIdentity


def main():
    output = ROOT / "output/persona-preview"
    output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(ROOT / "data/assets/manifest.json")
    resolver = AssetResolver(manifest)
    service = ArchiveService(ROOT / "data/p5x_zh.sqlite3", rows_per_page=7, max_pages=8)
    cases = []
    for slug, kind in [("anri-rinka", "daily"), ("joker", "character"), ("panther", "skills"), ("mont", "guide"), ("j-c", "character"), ("navi", "daily"), ("cattle", "daily")]:
        with sqlite3.connect(service.database_path.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT source_key, slug, name_zh, codename_zh, element_zh, position_zh, rarity FROM zh_characters WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise ValueError(slug)
        if kind == "daily":
            fortune = models.DailyFortune(date_key="2026-11-19", character=models.DailyCharacter(*row), tarot=tarot.draw(random.Random(42)))
            pages = (presenter.build_page(fortune),)
            command = "/p5x每日运势"
        else:
            result = (service.character_outcome(row[2]) if kind == "character" else service.skills_outcome(row[2]) if kind == "skills" else service.all_guides_outcome(row[2]))
            assert result.ok and result.pages, (slug, result.text)
            pages = result.pages
            command = {"character": "/p5x角色", "skills": "/p5x技能", "guide": "/p5x攻略"}[kind] + " " + row[2]
        cases.append((slug, command, pages))
    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        tab = browser.new_page(device_scale_factor=1)
        for background, size in BACKGROUND_SIZES.items():
            for slug, command, pages in cases:
                # 每张分页都验证，只在总览选第一页。
                for index, card in enumerate(pages):
                    visual = replace(visual_context.build(card, resolver=resolver, identity=RequesterIdentity("preview", "字号验收")), background_preset=background)
                    assert visual.persona_image, slug
                    stem = f"{background}-{slug}-{index + 1}"
                    html = output / f"{stem}.html"
                    html.write_text(image_templates.render_page(card, theme="light", visual=visual), encoding="utf-8")
                    tab.set_viewport_size({"width": size[0], "height": size[1]})
                    tab.goto(html.as_uri())
                    tab.evaluate("async () => {await document.fonts.ready; await Promise.all([...document.images].map(i => i.decode()));}")
                    checks = tab.evaluate("""() => {
                      const card = document.querySelector('.card').getBoundingClientRect();
                      const content = (document.querySelector('.foot') || document.querySelector('.content')).getBoundingClientRect();
                      const figures = [...document.querySelectorAll('.hero-figure,.persona-figure')].map(e => { const r=e.getBoundingClientRect(); return r.x>=0 && r.y>=0 && r.right<=card.right+1 && r.bottom<=card.bottom+1; });
                      return {footerInside:content.bottom<=card.bottom+1, figuresInside:figures.every(Boolean), horizontal:document.documentElement.scrollWidth<=card.width};
                    }""")
                    tab.locator(".card").screenshot(path=str(output / f"{stem}.png"))
                    records.append({"file": f"{stem}.png", "background": background, "slug": slug, "command": command, "page": index+1, "checks": checks})
            selected = [r for r in records if r["background"] == background and r["page"] == 1]
            cell_height = round(490 * size[1] / size[0]) + 36
            sheet = Image.new("RGB", (1500, 2 * cell_height), "#e5e7eb")
            draw = ImageDraw.Draw(sheet)
            for i, record in enumerate(selected):
                with Image.open(output / record["file"]) as image:
                    image.thumbnail((490, 470))
                    x, y = i % 3 * 500, i // 3 * cell_height
                    sheet.paste(image, (x, y))
                draw.text((x+8,y+cell_height-25), f'{record["slug"]} / {record["file"]}', fill="black")
            sheet.save(output / f"overview-{background}.jpg", quality=94)
        # 同角色、同背景，关闭开关时保留原有完整人物布局。
        for background in BACKGROUND_SIZES:
            card = cases[0][2][0]
            visual = replace(visual_context.build(card, resolver=resolver, settings=visual_context.VisualSettings(show_persona_art=False)), background_preset=background)
            assert visual.hero_image and not visual.persona_image
            html = output / f"{background}-off.html"
            html.write_text(image_templates.render_page(card, visual=visual), encoding="utf-8")
            tab.goto(html.as_uri())
            tab.evaluate("async () => { await document.fonts.ready; await Promise.all([...document.images].map(i=>i.decode())); }")
            tab.locator(".card").screenshot(path=str(output / f"{background}-off.png"))
        browser.close()
    service.close()
    (output / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r for r in records if not all(r["checks"].values())]
    print(json.dumps({"rendered": len(records), "layout_failures": failures}, ensure_ascii=False))


if __name__ == "__main__":
    main()
