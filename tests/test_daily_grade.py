"""塔罗吉凶评级与综合建议：五档、确定性、逆位降一档 + 例外表。

这一层必须纯规则：不联网、不调用模型，同一个输入永远同一个输出。
"""

from __future__ import annotations

import pytest

from archive.daily import fortune_grade, tarot

ORDER = {grade: index for index, grade in enumerate(fortune_grade.GRADES)}
OVERRIDE_KEYS = set(fortune_grade.REVERSED_OVERRIDES)


def test_grades_are_five_levels():
    assert fortune_grade.GRADES == ("大吉", "吉", "平", "凶", "大凶")


def test_every_card_has_a_valid_grade():
    for name, _, _ in tarot.ALL_CARDS:
        for reversed_ in (False, True):
            assert fortune_grade.grade_of(name, reversed_) in fortune_grade.GRADES


def test_major_arcana_use_fixed_table():
    assert fortune_grade.grade_of("XIX 太阳 The Sun") == "大吉"
    assert fortune_grade.grade_of("XXI 世界 The World") == "大吉"
    assert fortune_grade.grade_of("XVI 高塔 The Tower") == "大凶"
    assert fortune_grade.grade_of("XIII 死神 Death") == "凶"
    assert fortune_grade.grade_of("IX 隐士 The Hermit") == "平"


def test_major_name_survives_format_change():
    """牌名换写法（改序号、改英文）不该影响评级。"""
    assert fortune_grade.grade_of("19 太阳") == "大吉"
    assert fortune_grade.grade_of("太阳 The Sun") == "大吉"


def test_minor_arcana_use_suit_and_rank():
    assert fortune_grade.grade_of("权杖王牌") == "大吉"
    assert fortune_grade.grade_of("圣杯九") == "大吉"
    assert fortune_grade.grade_of("宝剑三") == "凶"
    assert fortune_grade.grade_of("宝剑十") == "大凶"
    assert fortune_grade.grade_of("星币十") == "大吉"


def test_all_minor_keys_resolve():
    for suit, entries in tarot.MINOR_ARCANA.items():
        for rank in tarot.MINOR_RANKS:
            assert fortune_grade.minor_key(f"{suit}{rank}") is not None
        assert len(entries) == len(tarot.MINOR_RANKS)


def test_reversed_downgrades_exactly_one_step():
    """逆位大方降档：平→凶、凶→大凶，不能连降。"""
    for name, _, _ in tarot.ALL_CARDS:
        if fortune_grade.normalized_name(name) in OVERRIDE_KEYS:
            continue
        upright = fortune_grade.grade_of(name, False)
        reversed_ = fortune_grade.grade_of(name, True)
        if upright == fortune_grade.GRADE_AWFUL:
            assert reversed_ == upright
            continue
        assert ORDER[reversed_] - ORDER[upright] == 1, name


def test_reversed_never_goes_worse_than_awful():
    assert fortune_grade.REVERSED_SHIFT["大凶"] == "大凶"
    for name, _, _ in tarot.ALL_CARDS:
        assert fortune_grade.grade_of(name, True) in fortune_grade.GRADES


def test_flat_becomes_bad_when_reversed():
    assert fortune_grade.grade_of("IX 隐士 The Hermit") == "平"
    assert fortune_grade.grade_of("IX 隐士 The Hermit", True) == "凶"
    assert fortune_grade.grade_of("倒吊人", True) == "凶"


def test_bad_becomes_awful_when_reversed():
    assert fortune_grade.grade_of("XIII 死神 Death") == "凶"
    assert fortune_grade.grade_of("XIII 死神 Death", True) == "大凶"


def test_overrides_where_reversed_is_better():
    """逆位在牌义上是解脱/转机的牌，不能被机械降档。"""
    # 恶魔逆位：挣脱束缚，正位才是凶
    assert fortune_grade.grade_of("XV 恶魔 The Devil") == "凶"
    assert fortune_grade.grade_of("XV 恶魔 The Devil", True) == "吉"
    # 高塔逆位：避开灾难
    assert fortune_grade.grade_of("XVI 高塔 The Tower") == "大凶"
    assert fortune_grade.grade_of("XVI 高塔 The Tower", True) == "平"
    # 月亮逆位：拨云见日
    assert fortune_grade.grade_of("XVIII 月亮 The Moon", True) == "吉"
    # 宝剑十逆位：否极泰来
    assert fortune_grade.grade_of("宝剑十") == "大凶"
    assert fortune_grade.grade_of("宝剑十", True) == "凶"


def test_overrides_where_reversed_is_worse():
    """逆位牌义是破裂/失控的，比「降一档」更重。"""
    assert fortune_grade.grade_of("VII 战车 The Chariot") == "大吉"
    assert fortune_grade.grade_of("VII 战车 The Chariot", True) == "凶"
    assert fortune_grade.grade_of("X 命运之轮 Wheel of Fortune", True) == "凶"
    assert fortune_grade.grade_of("X 命运之轮 Wheel of Fortune") == "大吉"
    assert fortune_grade.grade_of("星币王牌", True) == "凶"
    assert fortune_grade.grade_of("圣杯十", True) == "凶"
    assert fortune_grade.grade_of("星币十", True) == "凶"


def test_overrides_only_apply_when_reversed():
    for name in fortune_grade.REVERSED_OVERRIDES:
        for card_name, _, _ in tarot.ALL_CARDS:
            if fortune_grade.normalized_name(card_name) != name:
                continue
            assert fortune_grade.grade_of(card_name, False) == fortune_grade.base_grade(card_name)


def test_unknown_card_is_flat():
    assert fortune_grade.grade_of("") == fortune_grade.DEFAULT_GRADE
    assert fortune_grade.grade_of("不存在的牌") == fortune_grade.DEFAULT_GRADE
    assert fortune_grade.base_grade(None) == fortune_grade.DEFAULT_GRADE


@pytest.mark.parametrize("grade", fortune_grade.GRADES)
def test_guide_matches_every_grade(grade):
    suitable, avoid = fortune_grade.guide_of(grade)
    assert suitable and avoid
    assert suitable != avoid


def test_guide_falls_back_for_unknown_grade():
    assert fortune_grade.guide_of("???") == fortune_grade.GRADE_GUIDES[fortune_grade.DEFAULT_GRADE]


@pytest.mark.parametrize("grade", fortune_grade.GRADES)
def test_advice_mentions_card_position_meaning_and_grade(grade):
    name = "XIX 太阳 The Sun"
    advice = fortune_grade.build_advice(name, "成功、活力、光明", False, grade=grade)
    assert "太阳" in advice
    assert "正位" in advice
    assert "成功、活力、光明" in advice
    assert fortune_grade.GRADE_ACTIONS[grade] in advice


def test_advice_adds_tail_only_when_reversed():
    upright = fortune_grade.build_advice("星币王牌", "财富机遇", False)
    reversed_ = fortune_grade.build_advice("星币王牌", "错失良机", True)
    tail = fortune_grade.POSITION_TAILS[True]
    assert tail
    assert tail not in upright
    assert reversed_.endswith(tail)


def test_advice_without_meaning_still_reads():
    advice = fortune_grade.build_advice("星币王牌", "")
    assert "牌面释义暂缺" in advice
    assert "星币王牌" in advice


def test_advice_for_unknown_card_is_generic():
    advice = fortune_grade.build_advice("", "", False)
    assert advice == "牌面信息不足，今天按平常节奏走就好。"


def test_advice_is_deterministic():
    first = fortune_grade.build_advice("VI 恋人 The Lovers", "爱情、和谐", True)
    second = fortune_grade.build_advice("VI 恋人 The Lovers", "爱情、和谐", True)
    assert first == second


def test_describe_returns_every_field():
    payload = fortune_grade.describe("VI 恋人 The Lovers", True, "分歧冲突")
    assert set(payload) == {"grade", "suitable", "avoid", "advice"}
    assert payload["grade"] == "平"
    assert payload["advice"] == fortune_grade.build_advice(
        "VI 恋人 The Lovers", "分歧冲突", True
    )
