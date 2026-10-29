"""模板层测试：转义、外链、页脚位置、类别映射。

这些点任意一条出错，都会让「一段用户数据」变成「一段可执行标记」，
或者让渲染服务在没有外网的容器里卡住。
"""

from __future__ import annotations

import re

import pytest

from archive import cards, image_templates, notices, visual_context

ALL_TEMPLATES = (
    "base.html",
    "character.html",
    "skill.html",
    "weapon.html",
    "guide.html",
    "character_list.html",
)


def _page(**overrides) -> cards.CardPage:
    base = dict(
        category="角色",
        title="测试角色",
        subtitle="5★ · 火焰 · 优越",
        lead="一段导语",
        sections=(
            cards.Section(
                heading="技能",
                blocks=(cards.Block(kind="bullets", lines=("· 技能甲", "· 技能乙")),),
            ),
        ),
        page_index=1,
        page_total=1,
        source_label="Lufel 社区资料",
        translation_label="机器翻译",
        has_conflict=True,
        show_footer=True,
    )
    base.update(overrides)
    return cards.CardPage(**base)


@pytest.mark.parametrize("name", ALL_TEMPLATES)
def test_template_files_exist(name):
    assert (image_templates.TEMPLATE_DIR / name).is_file()


def test_available_templates_lists_all():
    names = set(image_templates.available_templates())
    assert set(ALL_TEMPLATES).issubset(names)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("角色", "character.html"),
        ("技能", "skill.html"),
        ("武器", "weapon.html"),
        ("角色列表", "character_list.html"),
        ("评价攻略", "guide.html"),
        ("综合攻略", "guide.html"),
        ("意识攻略", "guide.html"),
    ],
)
def test_template_for_category(category, expected):
    assert image_templates.template_for(category) == expected


def test_unknown_category_falls_back():
    assert image_templates.template_for("不存在的类别") == image_templates.FALLBACK_TEMPLATE


def test_user_data_is_escaped():
    page = _page(
        title="<script>alert(1)</script>",
        lead='引号 " 与 & 与 <b>标签</b>',
        sections=(
            cards.Section(
                heading="<img src=x>",
                blocks=(cards.Block(kind="para", lines=("正文 <svg onload=1>",)),),
            ),
        ),
    )
    html = image_templates.render_page(page)
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;b&gt;标签&lt;/b&gt;" in html
    assert "&lt;img src=x&gt;" in html
    assert "&lt;svg onload=1&gt;" in html
    assert "&amp;" in html


def test_no_external_resources():
    html = image_templates.render_page(_page())
    for forbidden in ("http://", "https://", "<script", "<link", "@import", "src="):
        assert forbidden not in html, f"卡片里不应出现 {forbidden}"


def test_card_width_in_range():
    html = image_templates.render_page(_page())
    assert "width: 820px" in html


def test_footer_only_on_last_page():
    page_one = _page(page_index=1, page_total=2, show_footer=False)
    page_two = _page(page_index=2, page_total=2, show_footer=True)

    html_one = image_templates.render_page(page_one)
    html_two = image_templates.render_page(page_two)

    assert "1/2" in html_one
    assert notices.DISCLAIMER_LINES[0] not in html_one
    assert "2/2" in html_two
    assert notices.DISCLAIMER_LINES[0] in html_two


def test_conflict_warning_rendered():
    html = image_templates.render_page(_page(has_conflict=True))
    assert notices.NUMERIC_CONFLICT_WARNING in html


def test_conflict_warning_absent_when_no_conflict():
    html = image_templates.render_page(_page(has_conflict=False))
    assert notices.NUMERIC_CONFLICT_WARNING not in html


def test_omitted_notice_rendered():
    html = image_templates.render_page(_page(omitted=7))
    assert "另有 7 项未展示" in html


def test_bullet_prefix_stripped_in_output():
    html = image_templates.render_page(_page())
    assert "· 技能甲" not in html
    assert "技能甲" in html


def test_theme_switch():
    dark = image_templates.render_page(_page(), theme="dark")
    light = image_templates.render_page(_page(), theme="light")
    assert 'data-theme="dark"' in dark
    assert 'data-theme="light"' in light
    # 非法主题回落到暗色，而不是抛错
    fallback = image_templates.render_page(_page(), theme="彩虹")
    assert 'data-theme="dark"' in fallback


def test_build_context_is_complete():
    context = image_templates.build_context(_page())
    for key in (
        "theme", "category", "title", "subtitle", "lead", "page_label",
        "sections", "omitted", "show_footer", "has_conflict", "conflict_warning",
        "source_label", "translation_label", "disclaimer_lines",
    ):
        assert key in context


def test_render_pages_returns_one_html_per_page():
    pages = [_page(page_index=1, page_total=2, show_footer=False), _page(page_index=2, page_total=2)]
    htmls = image_templates.render_pages(pages)
    assert len(htmls) == 2
    assert all(html.startswith("<!DOCTYPE html>") for html in htmls)


# ------------------------------------------------------------------ 视觉上下文


def _visual(**overrides) -> visual_context.VisualContext:
    base = dict(
        requester_name="山吹桐生",
        requester_avatar="data:image/webp;base64,AVATAR",
        hero_image="data:image/webp;base64,HERO",
        background_image="data:image/webp;base64,BG",
        hero_kind="character",
        has_requester_avatar=True,
        has_hero_image=True,
        show_requester=True,
    )
    base.update(overrides)
    return visual_context.VisualContext(**base)


def test_requester_header_rendered():
    html = image_templates.render_page(_page(), visual=_visual())
    assert "山吹桐生" in html
    assert "正在查阅 · 夜幕档案" in html
    assert "data:image/webp;base64,AVATAR" in html


def test_requester_header_hidden_by_visual_context():
    html = image_templates.render_page(
        _page(), visual=_visual(show_requester=False, requester_name="", requester_avatar="")
    )
    assert "正在查阅" not in html


def test_requester_without_avatar_shows_initial():
    html = image_templates.render_page(
        _page(),
        visual=_visual(requester_avatar="", has_requester_avatar=False),
    )
    assert 'class="requester-initial"' in html
    assert "山" in html


def test_requester_name_is_escaped():
    html = image_templates.render_page(
        _page(), visual=_visual(requester_name='<img src=x onerror="alert(1)">')
    )
    assert "&lt;img src=x" in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_hero_layers_rendered():
    html = image_templates.render_page(_page(), visual=_visual())
    assert 'class="hero-bg"' in html
    assert 'class="hero-scrim"' in html
    assert 'class="hero-figure"' in html
    assert "card has-hero" in html
    assert "data:image/webp;base64,HERO" in html
    assert "data:image/webp;base64,BG" in html


def test_no_hero_layers_without_image():
    html = image_templates.render_page(_page())
    assert 'class="hero-figure"' not in html
    assert "card has-hero" not in html


def test_opacity_and_blur_are_inlined():
    html = image_templates.render_page(
        _page(),
        visual=_visual(background_opacity=0.5, hero_opacity=0.7, background_blur=12),
    )
    assert "opacity:0.5" in html
    assert "opacity:0.7" in html
    assert "blur(12px)" in html


def test_only_data_uri_images():
    """卡片里只允许 data:image 的图片，绝不允许远程地址。"""
    html = image_templates.render_page(_page(), visual=_visual())
    sources = re.findall(r'src="([^"]*)"', html)
    assert sources
    for source in sources:
        assert source.startswith("data:image/"), source


def test_no_remote_url_anywhere_with_visual():
    html = image_templates.render_page(_page(), visual=_visual())
    assert "http://" not in html
    assert "https://" not in html


def test_thumbnails_rendered_for_item_blocks():
    page = _page(
        category="武器",
        sections=(
            cards.Section(
                heading="武器",
                blocks=(
                    cards.Block(kind="item", lines=("· 武器甲", "攻击 100"), ref_key="w1"),
                    cards.Block(kind="item", lines=("· 武器乙", "攻击 200"), ref_key="w2"),
                ),
            ),
        ),
    )
    visual = _visual(thumbs={"w1": "data:image/webp;base64,W1"})
    html = image_templates.render_page(page, visual=visual)
    assert 'class="item-thumb"' in html
    assert "data:image/webp;base64,W1" in html
    # 没登记的缩略图不渲染 img，但条目正文照旧
    assert "武器乙" in html
    assert html.count('class="item-thumb"') == 1


def test_unknown_ref_key_does_not_break():
    page = _page(
        category="武器",
        sections=(
            cards.Section(
                heading="武器",
                blocks=(cards.Block(kind="item", lines=("· 武器甲", "x"), ref_key="nope"),),
            ),
        ),
    )
    html = image_templates.render_page(page, visual=_visual(thumbs={}))
    assert "武器甲" in html
    assert 'class="item-thumb"' not in html


def test_render_pages_accepts_visuals():
    pages = [_page(page_index=1, page_total=2, show_footer=False), _page(page_index=2)]
    htmls = image_templates.render_pages(
        pages, visuals=[_visual(), _visual(hero_image="data:image/webp;base64,H2")]
    )
    assert len(htmls) == 2
    assert "data:image/webp;base64,H2" in htmls[1]


def test_render_pages_without_visuals_still_works():
    htmls = image_templates.render_pages([_page()])
    assert 'class="hero-figure"' not in htmls[0]


def test_build_context_includes_visual():
    context = image_templates.build_context(_page(), visual=_visual())
    assert context["visual"]["requester_name"] == "山吹桐生"
    assert context["visual"]["has_hero_image"] is True


def test_build_context_without_visual_uses_empty():
    context = image_templates.build_context(_page())
    assert context["visual"]["has_hero_image"] is False
    assert context["visual"]["show_requester"] is True


def test_opaque_hero_gets_mask_class():
    """不透明素材（武器照片、占位图）仍要水平遮罩融边。"""
    html = image_templates.render_page(_page(), visual=_visual(hero_transparent=False))
    assert 'class="hero-figure hero-figure--masked"' in html


def test_transparent_hero_has_no_mask_class():
    """透明底立绘不加遮罩，否则人物左半身会被一起淡掉。"""
    html = image_templates.render_page(_page(), visual=_visual())
    assert 'class="hero-figure"' in html
    assert 'class="hero-figure hero-figure--masked"' not in html


def test_wide_hero_switches_to_contain_class():
    """横构图立绘加 hero-art--wide，靠 CSS 换成 contain。"""
    html = image_templates.render_page(_page(), visual=_visual(hero_wide=True))
    assert 'class="hero-art hero-art--wide"' in html


def test_tall_hero_has_no_wide_class():
    html = image_templates.render_page(_page(), visual=_visual())
    assert 'class="hero-art"' in html
    assert 'class="hero-art hero-art--wide"' not in html
