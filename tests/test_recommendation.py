"""技能加点推荐：只认结构化字段，缺失必须显式说「暂未收录」。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from archive import recommendation


@dataclass(frozen=True)
class FakeSkill:
    skill_key: str
    name_zh: str = ""


SKILLS = (
    FakeSkill("skill1", "琴奏之调"),
    FakeSkill("skill2", "月鸣之炎"),
    FakeSkill("skill3", "焰华·绯月昂扬"),
)


def _content(priority, levels=None) -> dict:
    data = {"skill_priority": priority}
    if levels is not None:
        data["skill_levels"] = levels
    return data


# ------------------------------------------------------------------ 正常解析


def test_parses_priority_order():
    result = recommendation.parse(_content("1>3>2"), SKILLS)
    assert result.available is True
    assert result.ordered_skill_keys == ("skill1", "skill3", "skill2")
    assert result.ordered_skill_names == ("琴奏之调", "焰华·绯月昂扬", "月鸣之炎")
    assert result.priority_text == "技能1·琴奏之调 ＞ 技能3·焰华·绯月昂扬 ＞ 技能2·月鸣之炎"
    assert result.order_text == "琴奏之调 → 焰华·绯月昂扬 → 月鸣之炎"


def test_slot_four_becomes_battle_technique():
    result = recommendation.parse(_content("3>1>4>2"), SKILLS)
    assert recommendation.BATTLE_SLOT_LABEL in result.priority_labels
    # 战斗技巧不在 zh_skills 里，所以不进「推荐顺序」的技能名列表
    assert recommendation.BATTLE_SLOT_LABEL not in result.ordered_skill_names
    assert result.needs_review is False


def test_slot_four_uses_skill4_when_present():
    skills = SKILLS + (FakeSkill("skill4", "总攻击"),)
    result = recommendation.parse(_content("4>1"), skills)
    assert result.priority_labels[0] == "技能4·总攻击"
    assert result.ordered_skill_names[0] == "总攻击"
    assert result.needs_review is False


@pytest.mark.parametrize("separator", [">", "＞", "→", ",", "，", "、", "/", "／", " ", "  "])
def test_accepts_common_separators(separator):
    result = recommendation.parse(_content(f"1{separator}3"), SKILLS)
    assert result.available is True
    assert result.ordered_skill_keys == ("skill1", "skill3")


def test_level_summary_becomes_note():
    levels = {"skill1": "MAX!", "skill2": "MAX", "skill3": "MAX", "skill4": "MAX"}
    result = recommendation.parse(_content("1>2>3", levels), SKILLS)
    assert result.notes
    assert result.notes[0].startswith("加点目标：")
    assert "琴奏之调 MAX!" in result.notes[0]


def test_notes_are_capped_at_three():
    levels = {"skill1": "MAX", "skill2": "MAX", "skill3": "MAX", "skill4": "MAX"}
    result = recommendation.parse(_content("1>9>2", levels), SKILLS)
    assert len(result.notes) <= recommendation.MAX_NOTES


def test_status_fields_are_passed_through():
    result = recommendation.parse(
        _content("1"), SKILLS, source_status="community", translation_status="machine_translated"
    )
    assert result.source_status == "community"
    assert result.translation_status == "machine_translated"


def test_raw_priority_is_preserved():
    result = recommendation.parse(_content(" 1>3 "), SKILLS)
    assert result.raw_priority == "1>3"


# ------------------------------------------------------------------ 缺失与待核对


def test_missing_priority_is_unavailable():
    result = recommendation.parse({"skill_levels": {"skill1": "MAX"}}, SKILLS)
    assert result.available is False
    assert result.ordered_skill_names == ()
    assert recommendation.UNAVAILABLE_TEXT == "暂未收录可靠的技能加点顺序"


def test_none_content_is_unavailable():
    assert recommendation.parse(None, SKILLS).available is False


def test_non_dict_content_is_unavailable():
    assert recommendation.parse("1>2", SKILLS).available is False


def test_blank_priority_is_unavailable():
    assert recommendation.parse(_content("   "), SKILLS).available is False


def test_empty_priority_is_unavailable():
    assert recommendation.parse(_content(">>>"), SKILLS).available is False


def test_unknown_slot_keeps_number_and_flags_review():
    result = recommendation.parse(_content("1>7"), SKILLS)
    assert result.available is True
    assert result.priority_labels[-1] == "技能7"
    assert result.needs_review is True
    assert any("7" in note for note in result.notes)


def test_non_numeric_token_flags_review():
    result = recommendation.parse(_content("1>技能二"), SKILLS)
    assert result.needs_review is True
    assert result.available is True


def test_missing_skill_name_flags_review():
    skills = (FakeSkill("skill1", ""),)
    result = recommendation.parse(_content("1"), skills)
    assert result.needs_review is True
    assert "未收录名称" in result.priority_labels[0]


def test_unavailable_has_no_notes():
    assert recommendation.UNAVAILABLE.notes == ()
    assert recommendation.UNAVAILABLE.available is False


def test_review_suffix():
    from archive import notices

    result = recommendation.parse(_content("1>7"), SKILLS)
    assert result.review_suffix == notices.NEEDS_REVIEW_BADGE
    assert recommendation.UNAVAILABLE.review_suffix == ""


# ------------------------------------------------------------------ 真实数据


def test_real_setting_data_renders_recommendation(service):
    """真实库里有养成数据的角色，文字输出必须带「优先级 / 推荐顺序」。"""
    outcome = service.character_outcome("汐见琴音")
    assert outcome.ok
    assert "■ 技能加点推荐" in outcome.text
    assert "优先级：" in outcome.text
    assert "推荐顺序：" in outcome.text
    assert recommendation.UNAVAILABLE_TEXT not in outcome.text


def test_real_character_without_setting_is_unavailable(service):
    """坂本龙司·星舞 没有 setting 数据，必须显式「暂未收录」而不是隐藏板块。"""
    outcome = service.character_outcome("坂本龙司·星舞")
    assert outcome.ok
    assert "■ 技能加点推荐" in outcome.text
    assert recommendation.UNAVAILABLE_TEXT in outcome.text


def test_real_skill_names_are_chinese(service):
    """推荐顺序里出现的技能名必须来自技能表，不能是 skill1 这种内部键。"""
    outcome = service.character_outcome("汐见琴音")
    order_line = next(
        line for line in outcome.text.splitlines() if line.startswith("推荐顺序：")
    )
    assert "skill" not in order_line
    assert "琴奏之调" in order_line
