"""展示模型测试：结构化查询结果 → 卡片页。"""

from __future__ import annotations

import pytest

from archive import image_presenter, notices


def test_character_pages_structure(service):
    outcome = service.character_outcome("汐见琴音")
    pages = outcome.pages

    assert len(pages) == 1
    page = pages[0]
    assert page.category == "角色"
    assert page.title == "汐见琴音"
    assert "5★" in page.subtitle
    headings = [section.heading for section in page.sections]
    assert "技能" in headings and "武器" in headings
    assert page.show_footer is True
    assert page.source_label == notices.source_label("community")


def test_character_page_lead_uses_description(service):
    page = service.character_outcome("汐见琴音").pages[0]
    assert page.lead  # 有简介就放到导语里，没有也不能是 None


def test_skills_pages_carry_descriptions(service):
    page = service.skills_outcome("汐见琴音").pages[0]
    assert page.category == "技能"
    assert page.sections[0].heading.startswith("技能（共")
    titles = [block.title for block in page.sections[0].blocks]
    assert any("琴奏之调" in title for title in titles)


def test_weapons_pages_mark_missing_skill_name(service):
    page = service.weapons_outcome("汐见琴音").pages[0]
    assert page.category == "武器"
    text = "\n".join(
        line for section in page.sections for block in section.blocks for line in block.body
    )
    assert notices.MISSING_WEAPON_SKILL_NAME in text


@pytest.mark.parametrize(
    ("guide_type", "category"),
    [
        ("review", "评价攻略"),
        ("setting", "养成攻略"),
        ("operation", "操作攻略"),
        ("party", "配队攻略"),
        ("ritual", "意识攻略"),
        ("innate", "属性意识攻略"),
    ],
)
def test_guide_pages_category(service, guide_type, category):
    outcome = service.guide_outcome("汐见琴音", guide_type)
    assert outcome.pages[0].category == category
    assert outcome.pages[0].title == "汐见琴音"


def test_all_guides_pages_are_capped(service):
    outcome = service.all_guides_outcome("汐见琴音")
    pages = outcome.pages
    assert 1 < len(pages) <= service.max_pages
    assert pages[0].category == "综合攻略"
    assert [page.page_index for page in pages] == list(range(1, len(pages) + 1))
    assert [page.show_footer for page in pages] == [False] * (len(pages) - 1) + [True]


def test_character_list_pages_cover_all_characters(service):
    outcome = service.character_list_outcome()
    pages = outcome.pages
    total = sum(
        len(section.blocks) for page in pages for section in page.sections
    )
    assert total == service.count_characters()
    assert pages[0].category == "角色列表"
    assert pages[-1].show_footer is True


def test_conflict_flag_follows_profile(service):
    """查询层冲突角色（技能+武器范围）必须带上数值差异标记。"""
    from archive.repository import CONFLICT_SCOPE_CHARACTER

    conflicts = [
        character
        for character in service._repo.list_characters()
        if service._repo.build_profile(character, CONFLICT_SCOPE_CHARACTER).has_conflict
    ]
    assert conflicts, "测试数据里应当存在冲突角色"
    page = service.character_outcome(conflicts[0].display_name).pages[0]
    assert page.has_conflict is True


def test_no_conflict_page_has_no_flag(service):
    from archive.repository import CONFLICT_SCOPE_CHARACTER

    clean = next(
        character
        for character in service._repo.list_characters()
        if not service._repo.build_profile(character, CONFLICT_SCOPE_CHARACTER).has_conflict
    )
    page = service.character_outcome(clean.display_name).pages[0]
    assert page.has_conflict is False


def test_presenter_does_not_render_html(service):
    """展示层只产出模型，不应带 HTML 字符串。"""
    page = service.character_outcome("汐见琴音").pages[0]
    assert "<" not in page.title
    assert all("<" not in block.title for section in page.sections for block in section.blocks)
