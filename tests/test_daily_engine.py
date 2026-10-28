"""每日运势引擎：种子、时区、确定性。

这里验的是「同一天结果固定」这条硬要求 —— 它一旦破了，
用户第二天再看同一天的历史就会对不上，等于功能不可信。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

import pytest

from archive.daily import engine, models

DATE_KEY = "2026-10-27"


def make_characters(count: int = 72) -> list[models.DailyCharacter]:
    return [
        models.DailyCharacter(
            key=f"k{index:03d}",
            slug=f"s{index}",
            name=f"角色{index}",
            element="火焰",
            position="支配",
            rarity=5,
        )
        for index in range(count)
    ]


def generate(**overrides) -> models.DailyFortune:
    kwargs = {
        "date_key": DATE_KEY,
        "timezone": "Asia/Shanghai",
        "platform_id": "aiocqhttp",
        "user_id": "10001",
        "display_name": "山吹桐生",
        "characters": make_characters(),
    }
    kwargs.update(overrides)
    return engine.generate(**kwargs)


# ------------------------------------------------------------------ 种子


def test_seed_is_sha256_of_salted_triplet():
    expected = hashlib.sha256(
        f"fortune|{DATE_KEY}|aiocqhttp|10001".encode("utf-8")
    ).digest()
    assert engine.fortune_seed(DATE_KEY, "aiocqhttp", "10001") == expected
    assert len(expected) == 32


def test_character_and_fortune_seeds_are_isolated():
    fortune = engine.fortune_seed(DATE_KEY, "aiocqhttp", "10001")
    character = engine.character_seed(DATE_KEY, "aiocqhttp", "10001")
    assert fortune != character
    assert character == hashlib.sha256(
        f"character|{DATE_KEY}|aiocqhttp|10001".encode("utf-8")
    ).digest()


def test_seed_is_not_md5():
    """参考实现用的是 MD5，本实现必须换掉 —— 16 字节是 MD5 的特征。"""
    assert len(engine.fortune_seed(DATE_KEY, "p", "u")) == 32


def test_seed_changes_with_every_component():
    base = engine.fortune_seed(DATE_KEY, "aiocqhttp", "10001")
    assert engine.fortune_seed("2026-10-28", "aiocqhttp", "10001") != base
    assert engine.fortune_seed(DATE_KEY, "other", "10001") != base
    assert engine.fortune_seed(DATE_KEY, "aiocqhttp", "10002") != base


def test_empty_user_id_is_allowed_and_stable():
    first = engine.fortune_seed(DATE_KEY, "p", "")
    second = engine.fortune_seed(DATE_KEY, "p", "")
    assert first == second


# ------------------------------------------------------------------ 确定性


def test_same_user_same_day_is_stable_for_100_calls():
    baseline = generate().to_payload()
    for _ in range(99):
        assert generate().to_payload() == baseline


def test_character_pool_order_does_not_matter():
    """候选池顺序变了结果也不能变 —— 数据库换个查询计划不该改运势。"""
    characters = make_characters()
    baseline = generate(characters=characters).to_payload()
    assert generate(characters=list(reversed(characters))).to_payload() == baseline
    assert generate(characters=characters[10:] + characters[:10]).to_payload() == baseline


def test_duplicate_candidates_do_not_change_result():
    characters = make_characters()
    doubled = characters + characters[:5]
    assert generate(characters=doubled).to_payload() == generate(characters=characters).to_payload()


def test_rename_keeps_result_but_updates_display_name():
    before = generate(display_name="旧名字")
    after = generate(display_name="新名字")
    assert before.to_payload() == after.to_payload()
    assert after.display_name == "新名字"


def test_next_day_refreshes():
    today = generate()
    tomorrow = generate(date_key="2026-10-28")
    assert today.to_payload() != tomorrow.to_payload()


def test_character_can_repeat_on_other_days():
    """次日允许再抽到同一个角色 —— 不允许的是「同一天」变来变去。"""
    start = date(2026, 1, 1)
    keys = [
        generate(date_key=(start + timedelta(days=offset)).isoformat()).character.key
        for offset in range(200)
    ]
    assert len(keys) == 200
    assert len(set(keys)) < 200


def test_different_users_get_different_fortunes():
    payloads = {
        generate(user_id=f"user{index}").to_payload()["tarot"]["name"]
        for index in range(30)
    }
    assert len(payloads) > 5


def test_same_user_id_on_different_platforms_is_not_shared():
    """跨平台同数字 ID 必须是两个人。"""
    left = generate(platform_id="aiocqhttp", user_id="10001")
    right = generate(platform_id="telegram", user_id="10001")
    assert left.to_payload() != right.to_payload()


# ------------------------------------------------------------------ 时区


def test_invalid_timezone_falls_back_with_warning(caplog):
    zone, name = engine.resolve_timezone("Mars/Olympus")
    assert name == engine.DEFAULT_TIMEZONE
    assert zone.key == engine.DEFAULT_TIMEZONE
    assert any("daily_fortune_timezone" in record.message for record in caplog.records)


def test_blank_timezone_uses_default():
    _, name = engine.resolve_timezone("   ")
    assert name == engine.DEFAULT_TIMEZONE


def test_today_in_uses_configured_timezone():
    zone, _ = engine.resolve_timezone("Asia/Shanghai")
    # UTC 2026-10-27 17:00 = 上海 2026-10-28 01:00
    moment = datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc)
    assert engine.today_in(zone, moment) == date(2026, 10, 28)

    utc_zone, _ = engine.resolve_timezone("UTC")
    assert engine.today_in(utc_zone, moment) == date(2026, 10, 27)


def test_today_in_accepts_naive_datetime():
    zone, _ = engine.resolve_timezone("Asia/Shanghai")
    assert engine.today_in(zone, datetime(2026, 10, 27, 12, 0)) == date(2026, 10, 27)


# ------------------------------------------------------------------ 内容


def test_ganzhi_is_computed_from_date():
    assert generate().ganzhi == "甲戌"


def test_payload_round_trip_keeps_every_field():
    fortune = generate()
    restored = models.DailyFortune.from_payload(
        fortune.to_payload(),
        date_key=fortune.date_key,
        timezone=fortune.timezone,
        platform_id=fortune.platform_id,
        user_id=fortune.user_id,
        display_name=fortune.display_name,
    )
    assert restored.character == fortune.character
    assert restored.tarot == fortune.tarot
    assert restored.stored is True


def test_grade_is_derived_and_stable():
    """评级不进 payload，但每次算都一样。"""
    fortune = generate()
    restored = models.DailyFortune.from_payload(
        fortune.to_payload(),
        date_key=fortune.date_key,
        timezone=fortune.timezone,
        platform_id=fortune.platform_id,
        user_id=fortune.user_id,
        display_name=fortune.display_name,
    )
    assert fortune.grade in ("大吉", "吉", "平", "凶", "大凶")
    assert restored.grade == fortune.grade
    assert restored.guide == fortune.guide
    assert restored.advice == fortune.advice
    assert "grade" not in fortune.to_payload()
    assert "advice" not in fortune.to_payload()


def test_empty_pool_raises():
    with pytest.raises(engine.FortuneUnavailableError):
        generate(characters=[])


def test_invalid_date_key_raises():
    with pytest.raises(engine.FortuneUnavailableError):
        generate(date_key="2026/10/27")


def test_draw_fortune_returns_character_and_tarot():
    chosen, card = engine.draw_fortune(
        date_key=DATE_KEY,
        platform_id="aiocqhttp",
        user_id="10001",
        characters=make_characters(),
    )
    assert chosen.key
    assert card.name and card.meaning


def test_date_label_is_human_readable():
    fortune = generate()
    assert fortune.date_label == "2026 年 10 月 27 日"
    assert fortune.day_label.startswith("2026 年 10 月 27 日")


def test_models_tolerate_broken_payload():
    broken = models.DailyFortune.from_payload(
        {"tarot": "not-a-dict", "character": None},
        date_key=DATE_KEY,
        timezone="Asia/Shanghai",
        platform_id="p",
        user_id="u",
        display_name="n",
    )
    assert broken.tarot.name == ""
    assert broken.character.key == ""


def test_legacy_payload_with_removed_sections_is_readable():
    """旧记录里还留着 lingqian / zhouyi，读回来只取仍在用的字段。"""
    legacy = {
        "character": {"key": "k1", "name": "角色一"},
        "tarot": {"name": "愚者", "is_reversed": False, "meaning": "新的开始"},
        "lingqian": {"number": 1, "grade": "上上", "poem": "旧签诗"},
        "zhouyi": {"number": 1, "name": "乾"},
        "ganzhi": "甲戌",
        "advice": "旧建议",
    }
    restored = models.DailyFortune.from_payload(
        legacy,
        date_key=DATE_KEY,
        timezone="Asia/Shanghai",
        platform_id="p",
        user_id="u",
        display_name="n",
    )
    assert restored.character.name == "角色一"
    assert restored.tarot.name == "愚者"
    assert not hasattr(restored, "lingqian")
    assert not hasattr(restored, "zhouyi")
    # 旧记录里存的 advice 不再采信：建议现在按牌名/正逆位/牌义现算
    assert "旧建议" not in restored.advice
