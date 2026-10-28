"""每日运势展示：文字格式、卡片结构、模板选择、免责声明不串台。"""

from __future__ import annotations

import pytest

from archive.daily import engine, models, presenter

pytest.importorskip("jinja2")
pytest.importorskip("PIL")

from archive import asset_manifest, asset_resolver, image_templates, visual_context  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

DATE_KEY = "2026-10-27"


def make_fortune(**overrides) -> models.DailyFortune:
    characters = [
        models.DailyCharacter(
            key=f"k{index:03d}",
            slug=f"s{index}",
            name=f"角色{index}",
            element="火焰",
            position="支配",
            rarity=5,
        )
        for index in range(20)
    ]
    kwargs = {
        "date_key": DATE_KEY,
        "timezone": "Asia/Shanghai",
        "platform_id": "aiocqhttp",
        "user_id": "10001",
        "display_name": "山吹桐生",
        "characters": characters,
    }
    kwargs.update(overrides)
    return engine.generate(**kwargs)


# ------------------------------------------------------------------ 文字


def test_text_contains_every_section():
    text = presenter.render_text(make_fortune())
    assert "【P5X 每日运势】" in text
    assert "2026 年 10 月 27 日" in text
    assert "今日角色：" in text
    assert "塔罗指引：" in text


def test_text_omits_removed_sections():
    text = presenter.render_text(make_fortune())
    assert "观音灵签" not in text
    assert "周易" not in text
    assert "卦" not in text
    assert "动爻" not in text


def test_text_contains_grade_guide_and_advice():
    fortune = make_fortune()
    text = presenter.render_text(fortune)
    assert f"· {fortune.grade}" in text
    suitable, avoid = fortune.guide
    assert f"宜：{suitable}" in text
    assert f"忌：{avoid}" in text
    assert f"综合建议：{fortune.advice}" in text
    # 建议里必须带上这张牌的牌义，而不是一句通用的套话
    assert fortune.tarot.meaning in fortune.advice


def test_tarot_label_drops_number_and_english():
    fortune = make_fortune()
    for day in range(1, 15):
        drawn = make_fortune(date_key=f"2027-01-{day:02d}").tarot
        label = presenter.tarot_label(drawn)
        assert drawn.position in label
        assert " The " not in label and "The " not in label


def test_card_block_carries_grade():
    fortune = make_fortune()
    page = presenter.build_page(fortune)
    block = page.sections[0].blocks[0]
    assert f"· {fortune.grade}" in block.title
    assert any(line.startswith("宜：") for line in block.body)
    assert any(line.startswith("忌：") for line in block.body)


def test_text_ends_with_entertainment_note():
    text = presenter.render_text(make_fortune())
    assert text.rstrip().endswith(presenter.ENTERTAINMENT_NOTE)
    assert "仅供娱乐参考" in text


def test_text_fits_message_budget():
    for day in range(1, 29):
        text = presenter.render_text(make_fortune(date_key=f"2026-11-{day:02d}"))
        assert len(text) <= 1200


def test_text_is_short_for_every_day():
    """砍掉两块后，文字长度应稳定在一屏内。"""
    for day in range(1, 29):
        text = presenter.render_text(make_fortune(date_key=f"2026-12-{day:02d}"))
        assert len(text) <= 400


def test_text_does_not_leak_community_disclaimer():
    text = presenter.render_text(make_fortune())
    assert "Lufel" not in text
    assert "机器翻译" not in text


# ------------------------------------------------------------------ 卡片


def test_page_structure():
    fortune = make_fortune()
    page = presenter.build_page(fortune)

    assert page.category == presenter.CATEGORY == "每日运势"
    assert page.title == fortune.character.name
    assert fortune.character.element in page.subtitle
    assert "2026 年 10 月 27 日" in page.lead
    assert page.page_total == 1
    assert page.show_footer is True
    assert page.hero_kind == "character"
    assert page.hero_key == fortune.character.slug
    assert page.hero_variant == DATE_KEY


def test_page_has_tarot_and_advice_sections():
    headings = [section.heading for section in presenter.build_sections(make_fortune())]
    assert headings == ["塔罗指引", "综合建议"]


def test_page_disclaimer_is_daily_specific():
    page = presenter.build_page(make_fortune())
    assert page.disclaimer_lines == presenter.DISCLAIMER_LINES
    assert any("仅供娱乐参考" in line for line in page.disclaimer_lines)
    assert not any("Lufel" in line for line in page.disclaimer_lines)


def test_template_is_registered():
    assert image_templates.template_for("每日运势") == "daily_fortune.html"
    assert "daily_fortune.html" in image_templates.available_templates()


def test_render_page_uses_daily_template():
    page = presenter.build_page(make_fortune())
    html = image_templates.render_page(page, theme="dark")
    # 运势卡把类别标签换成了「今日角色」，模板专属文案要在这里出现
    assert "今日角色" in html
    assert "塔罗指引" in html
    assert "仅供娱乐参考" in html
    assert "Lufel" not in html
    assert "机器翻译" not in html


def test_render_page_escapes_character_name():
    fortune = make_fortune()
    hostile = models.DailyFortune(
        **{
            **fortune.__dict__,
            "character": models.DailyCharacter(
                key="x", slug="x", name="<script>alert(1)</script>", element="火焰"
            ),
        }
    )
    html = image_templates.render_page(presenter.build_page(hostile), theme="dark")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_page_light_theme_works():
    page = presenter.build_page(make_fortune())
    html = image_templates.render_page(page, theme="light")
    assert 'data-theme="light"' in html


# ------------------------------------------------------------------ 缺图


def make_assets(tmp_path, *, slug: str, count: int = 2):
    """造一份清单：指定 slug 有 count 张立绘，外加一张角色占位图。"""
    root = tmp_path / "assets"
    folder = root / "characters" / slug
    folder.mkdir(parents=True)
    paths = []
    for index in range(count):
        target = folder / f"{index + 1}.webp"
        PILImage.new("RGB", (90, 120), (30, 30, 40)).save(target, format="WEBP")
        paths.append(target.relative_to(root).as_posix())

    fallback = root / "fallback"
    fallback.mkdir(parents=True)
    PILImage.new("RGB", (90, 120), (18, 18, 24)).save(
        fallback / "character.webp", format="WEBP"
    )

    data = {
        "characters": {
            slug: {
                "portrait": paths[0],
                "portraits": [{"path": path} for path in paths],
                "background": paths[0],
            }
        }
    }
    return asset_manifest.parse_manifest(data, root)


def test_visual_uses_manifest_portrait(tmp_path):
    fortune = make_fortune()
    page = presenter.build_page(fortune)
    resolver = asset_resolver.AssetResolver(make_assets(tmp_path, slug=fortune.character.slug))
    visual = visual_context.build(page, resolver=resolver)
    assert visual.has_hero_image is True
    assert visual.hero_image.startswith("data:image/")


def test_missing_art_falls_back_to_placeholder_without_redraw(tmp_path):
    """缺图只影响视觉：角色与塔罗都不能变。"""
    fortune = make_fortune()
    page = presenter.build_page(fortune)
    # 清单里没有这个角色，但占位图在
    manifest = make_assets(tmp_path, slug="someone-else")
    visual = visual_context.build(page, resolver=asset_resolver.AssetResolver(manifest))

    assert visual.hero_kind == "character"
    assert visual.has_hero_image is True  # 退到角色占位图，不是重新抽角色
    rebuilt = presenter.build_page(fortune)
    assert rebuilt.title == page.title
    assert rebuilt.hero_key == page.hero_key
    assert rebuilt.hero_variant == page.hero_variant


def test_visual_variant_is_stable_for_same_day(tmp_path):
    fortune = make_fortune()
    resolver = asset_resolver.AssetResolver(make_assets(tmp_path, slug=fortune.character.slug))

    first = visual_context.build(presenter.build_page(fortune), resolver=resolver)
    second = visual_context.build(presenter.build_page(fortune), resolver=resolver)
    assert first.hero_image == second.hero_image
