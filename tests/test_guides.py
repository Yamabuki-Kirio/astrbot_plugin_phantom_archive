"""第二阶段攻略查询：六类资料 + 综合攻略。"""

from __future__ import annotations

import re

from archive import notices

HANGUL = re.compile(r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]")


GUIDE_CASES = (
    ("ritual", "ritual_message", "意识"),
    ("innate", "innate_message", "属性意识"),
    ("setting", "setting_message", "养成"),
    ("operation", "operation_message", "操作"),
    ("review", "review_message", "评价"),
    ("party", "party_message", "配队"),
)


def _message(service, term: str, guide_type: str) -> str:
    return service.guide_message(term, guide_type)


def test_kotone_all_primary_guides(service):
    for guide_type, _, title in GUIDE_CASES:
        text = _message(service, "汐见琴音", guide_type)
        assert text.startswith(f"【汐见琴音】{title}")
        assert notices.DISCLAIMER in text
        assert len(text) <= service.max_chars


def test_known_content_is_rendered(service):
    assert "伟大的抉择" in _message(service, "汐见琴音", "ritual")
    assert "增益复制辅助" in _message(service, "汐见琴音", "setting")
    assert "全力全开" in _message(service, "汐见琴音", "operation")
    assert "雨宫莲·星舞" in _message(service, "汐见琴音", "party")
    assert "群体诅咒输出" in _message(service, "雨宫莲", "review")


def test_missing_innate_degrades_cleanly(service):
    text = _message(service, "汐见琴音", "innate")
    assert "暂无可显示的属性意识资料" in text
    assert notices.DISCLAIMER in text


def test_all_guides_obeys_budget(service):
    text = service.all_guides_message("汐见琴音")
    assert text.startswith("【汐见琴音】综合攻略")
    assert notices.DISCLAIMER in text
    assert len(text) <= service.max_chars


def test_empty_and_unknown_terms(service):
    assert service.guide_message("", "party").startswith("用法：/p5x配队")
    assert "没有找到" in service.guide_message("不存在角色XYZ", "ritual")


def test_all_characters_all_guides_smoke(service, repository):
    guide_types = ("review", "setting", "operation", "party", "ritual", "innate")
    for character in repository.list_characters():
        for guide_type in guide_types:
            text = service.guide_message(character.source_key, guide_type)
            assert text
            assert notices.DISCLAIMER in text
            assert len(text) <= service.max_chars
            assert not HANGUL.search(text), f"{character.name_zh}/{guide_type} 含韩文"
        combined = service.all_guides_message(character.source_key)
        assert combined
        assert len(combined) <= service.max_chars
        assert not HANGUL.search(combined), f"{character.name_zh}/综合攻略含韩文"
