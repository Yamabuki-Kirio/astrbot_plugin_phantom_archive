"""模板层测试：转义、外链、页脚位置、类别映射。

这些点任意一条出错，都会让「一段用户数据」变成「一段可执行标记」，
或者让渲染服务在没有外网的容器里卡住。
"""

from __future__ import annotations

import pytest

from archive import cards, image_templates, notices

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
