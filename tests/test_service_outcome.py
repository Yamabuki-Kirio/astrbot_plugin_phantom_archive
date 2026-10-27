"""查询层结构化结果测试。

约定：``outcome.text`` 必须与旧的纯文字接口逐字一致 —— 图片只是多了一条路径，
不能顺手改掉文字降级看到的内容。
"""

from __future__ import annotations

import pytest

from archive import notices
from archive.service import ArchiveService, QueryOutcome


def test_character_outcome_ok(service):
    outcome = service.character_outcome("汐见琴音")
    assert isinstance(outcome, QueryOutcome)
    assert outcome.ok is True
    assert outcome.has_pages is True
    assert outcome.error_kind == ""


def test_text_matches_legacy_message(service):
    cases = (
        ("character_outcome", "character_message", ("汐见琴音",)),
        ("skills_outcome", "skills_message", ("汐见琴音",)),
        ("weapons_outcome", "weapons_message", ("汐见琴音",)),
        ("character_list_outcome", "character_list_message", ()),
        ("all_guides_outcome", "all_guides_message", ("汐见琴音",)),
        ("guide_outcome", "guide_message", ("汐见琴音", "ritual")),
    )
    for outcome_name, message_name, args in cases:
        outcome = getattr(service, outcome_name)(*args)
        assert outcome.text == getattr(service, message_name)(*args), outcome_name


def test_empty_term_is_usage_error(service):
    outcome = service.character_outcome("   ")
    assert outcome.ok is False
    assert outcome.has_pages is False
    assert outcome.error_kind == "usage"
    assert outcome.text.startswith("用法：/p5x角色")


def test_unknown_term_is_not_found(service):
    outcome = service.skills_outcome("绝对不存在的角色XYZ")
    assert outcome.ok is False
    assert outcome.error_kind == "not_found"
    assert "没有找到" in outcome.text


def test_ambiguous_term_lists_candidates(service):
    outcome = service.weapons_outcome("琴音")
    assert outcome.ok is False
    assert outcome.error_kind == "ambiguous"
    assert "找到多个可能的角色" in outcome.text


def test_errors_never_carry_pages(service):
    for outcome in (
        service.character_outcome(""),
        service.character_outcome("绝对不存在的角色XYZ"),
        service.character_outcome("琴音"),
    ):
        assert outcome.pages == ()


def test_guide_outcome_rejects_unknown_type(service):
    with pytest.raises(ValueError):
        service.guide_outcome("汐见琴音", "不存在的类型")


def test_outcome_text_keeps_disclaimer(service):
    outcome = service.character_outcome("汐见琴音")
    assert notices.DISCLAIMER in outcome.text


def test_service_respects_paging_settings(db_path):
    tight = ArchiveService(db_path, max_chars=1200, rows_per_page=6, max_pages=2)
    try:
        outcome = tight.all_guides_outcome("汐见琴音")
        assert len(outcome.pages) <= 2
        assert outcome.pages[-1].omitted > 0
    finally:
        tight.close()


def test_service_does_not_write_database(service, db_path):
    """查询若干次后数据库文件不应被改动（只读三层仍然有效）。"""
    import hashlib

    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    for _ in range(3):
        service.character_outcome("汐见琴音")
        service.all_guides_outcome("汐见琴音")
        service.character_list_outcome()
    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert before == after
