"""分页与块分类的单元测试 —— 这两件事决定「卡片会不会切半句话」。"""

from __future__ import annotations

import pytest

from archive import cards


def test_single_bullet_is_bullets():
    block = cards.classify_block(["· 琴奏之调（增益 · SP 20）"])
    assert block.kind == "bullets"


def test_single_kv_line_is_kv():
    block = cards.classify_block(["定位：增益复制辅助"])
    assert block.kind == "kv"


def test_item_with_indented_body():
    block = cards.classify_block(["· 孔雀御前・改 5★", "  攻击 713.51", "  技能名称：暂未收录"])
    assert block.kind == "item"
    assert block.title == "孔雀御前・改 5★"
    assert block.body == ("攻击 713.51", "技能名称：暂未收录")


def test_all_bullet_lines_are_bullets_not_item():
    block = cards.classify_block(["· 优点一", "· 优点二"])
    assert block.kind == "bullets"
    assert block.rows == 2


def test_mixed_lines_fall_back_to_para():
    block = cards.classify_block(["正文第一行", "· 看起来像条目", "正文第三行"])
    assert block.kind == "para"


def test_empty_block_is_para():
    assert cards.classify_block([]).kind == "para"


def test_split_sections_attaches_blocks_to_heading():
    sections = cards.split_sections(
        [
            ["■ 优点", "· a", "· b"],
            ["■ 注意", "· c"],
        ]
    )
    assert [s.heading for s in sections] == ["优点", "注意"]
    assert sections[0].blocks[0].kind == "bullets"


def test_split_sections_without_heading_keeps_content():
    sections = cards.split_sections([["一段没有小节标题的正文"]])
    assert len(sections) == 1
    assert sections[0].heading == ""
    assert sections[0].blocks


def test_paginate_single_page_when_content_fits():
    sections = cards.split_sections([["■ 技能", "· a", "· b"]])
    pages = cards.paginate("角色", "测试", sections, rows_per_page=28, max_pages=4)
    assert len(pages) == 1
    assert pages[0].page_total == 1
    assert pages[0].show_footer is True
    assert pages[0].page_label == ""


def _raw_blocks(count: int, *, heading: str = "列表") -> list[list[str]]:
    """构造「一个小节标题 + N 个两行条目」，每个条目是独立块。"""
    return [[f"■ {heading}"]] + [[f"· 条目{i}", f"  说明{i}"] for i in range(count)]


def test_paginate_splits_by_whole_blocks():
    """20 个两行块，每页 12 行 → 必须整块切分，不能出现半个条目。"""
    sections = cards.split_sections(_raw_blocks(20))
    pages = cards.paginate("角色", "测试", sections, rows_per_page=12, max_pages=10)

    assert len(pages) > 1
    titles = [block.title for page in pages for section in page.sections for block in section.blocks]
    assert len(titles) == len(set(titles))  # 没有条目被重复放进两页
    assert len(titles) == 20  # 也没有条目被丢掉


def test_paginate_marks_footer_only_on_last_page():
    sections = cards.split_sections(_raw_blocks(40))
    pages = cards.paginate("角色", "测试", sections, rows_per_page=8, max_pages=10)

    assert len(pages) > 1
    assert [page.show_footer for page in pages] == [False] * (len(pages) - 1) + [True]


def test_paginate_counts_omitted_when_page_limit_hit():
    sections = cards.split_sections(_raw_blocks(60))
    pages = cards.paginate("角色", "测试", sections, rows_per_page=5, max_pages=2)

    assert len(pages) == 2
    assert pages[-1].omitted > 0
    assert pages[0].omitted == 0  # 只有最后一张提示少看了多少


def test_paginate_lead_only_on_first_page():
    sections = cards.split_sections(_raw_blocks(30))
    pages = cards.paginate(
        "角色", "测试", sections, lead="这是一段导语", rows_per_page=6, max_pages=8
    )
    assert pages[0].lead == "这是一段导语"
    assert all(page.lead == "" for page in pages[1:])


def test_paginate_continuation_heading():
    sections = cards.split_sections(_raw_blocks(30, heading="意识"))
    pages = cards.paginate("角色", "测试", sections, rows_per_page=6, max_pages=8)
    headings = [section.heading for page in pages for section in page.sections]
    assert headings[0] == "意识"
    assert any(heading.endswith("（续）") for heading in headings)


def test_oversized_block_gets_its_own_page():
    """单个块比整页还高时宁可溢出，也不能把它切两半。"""
    huge = ["· 超长条目"] + [f"  第 {i} 行" for i in range(40)]
    sections = cards.split_sections([["■ 列表"], huge, ["· 短条目"]])
    pages = cards.paginate("角色", "测试", sections, rows_per_page=10, max_pages=4)

    assert len(pages) >= 2
    assert pages[0].sections[0].blocks[0].title == "超长条目"


def test_with_footer_sets_source_and_conflict():
    page = cards.CardPage(category="角色", title="测试")
    updated = cards.with_footer(
        page, source_label="Lufel 社区资料", translation_label="机器翻译", has_conflict=True
    )
    assert updated.source_label == "Lufel 社区资料"
    assert updated.has_conflict is True
    assert updated.title == page.title


def test_page_label_format():
    page = cards.CardPage(category="角色", title="测试", page_index=2, page_total=3)
    assert page.page_label == "2/3"
