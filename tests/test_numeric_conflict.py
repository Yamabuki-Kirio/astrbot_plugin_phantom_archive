"""数值冲突提示：范围正确、正反例都对（§12 / §15）。

期望值一律从数据库现算，不写死角色名 —— 否则数据更新后测试会假通过。
只有几个具体角色作为回归锚点。
"""

from __future__ import annotations

import pytest

from archive import notices
from archive.repository import (
    CONFLICT_SCOPE_CHARACTER,
    CONFLICT_SCOPE_SKILL,
    CONFLICT_SCOPE_WEAPON,
)

WARNING = notices.NUMERIC_CONFLICT_WARNING


def _keys_with_conflict(repository, scope) -> set[str]:
    rows = repository._db.fetch_all(
        "SELECT DISTINCT character_key FROM numeric_conflicts WHERE numeric_conflict = 1"
    )
    keys = {r["character_key"] for r in rows}
    return {k for k in keys if repository.conflict_count(k, scope) > 0}


def test_character_scope_is_skill_plus_weapon():
    assert set(CONFLICT_SCOPE_CHARACTER) == {"skill", "weapon"}
    assert CONFLICT_SCOPE_SKILL == ("skill",)
    assert CONFLICT_SCOPE_WEAPON == ("weapon",)


def test_conflict_characters_all_warn(service, repository):
    keys = _keys_with_conflict(repository, CONFLICT_SCOPE_CHARACTER)
    assert keys, "数据库里应当存在带冲突的角色"
    for key in keys:
        character = repository.character_by_source_key(key)
        text = service.character_message(character.source_key)
        assert WARNING in text, f"{character.name_zh} 有冲突却没提示"


def test_clean_characters_never_warn(service, repository):
    conflicted = _keys_with_conflict(repository, CONFLICT_SCOPE_CHARACTER)
    for character in repository.list_characters():
        if character.source_key in conflicted:
            continue
        assert WARNING not in service.character_message(character.source_key), (
            f"{character.name_zh} 没有技能/武器冲突却提示了"
        )


def test_review_only_conflict_does_not_warn(service, repository):
    """YUI 的冲突只在角色评价里，本阶段不展示评价，因此不该提示。"""
    assert repository.conflict_count("YUI", CONFLICT_SCOPE_CHARACTER) == 0
    assert repository.conflict_count("YUI", ("review", "ritual")) > 0
    assert WARNING not in service.character_message("YUI")


def test_weapon_only_conflict_scoping(service, repository):
    """长尾千津子只有武器冲突：武器提示、技能不提示、角色提示。"""
    key = "치즈코"
    assert repository.conflict_count(key, CONFLICT_SCOPE_WEAPON) > 0
    assert repository.conflict_count(key, CONFLICT_SCOPE_SKILL) == 0

    assert WARNING in service.weapons_message("长尾千津子")
    assert WARNING not in service.skills_message("长尾千津子")
    assert WARNING in service.character_message("长尾千津子")


def test_skill_only_conflict_scoping(service, repository):
    """梨本成瑠海只有技能冲突：技能提示、武器不提示。"""
    key = "나루미"
    assert repository.conflict_count(key, CONFLICT_SCOPE_SKILL) > 0
    assert repository.conflict_count(key, CONFLICT_SCOPE_WEAPON) == 0

    assert WARNING in service.skills_message("梨本成瑠海")
    assert WARNING not in service.weapons_message("梨本成瑠海")
    assert WARNING in service.character_message("梨本成瑠海")


@pytest.mark.parametrize("term", ["长尾千津子", "梨本成瑠海", "雨宫莲·星舞"])
def test_warning_sits_before_disclaimer(service, term):
    text = service.character_message(term)
    assert WARNING in text, f"{term} 应当有冲突提示"
    # 警告必须在免责声明之前，且两者都在尾部（不会被正文挤掉）
    assert text.index(WARNING) < text.index(notices.DISCLAIMER)
    # 警告行必须完整，不能被截断
    assert WARNING in text.splitlines()


def test_warning_never_exposes_baseline_numbers(service):
    """不得把内部韩文基准文本或原始数字串直接丢给用户。"""
    text = service.character_message("长尾千津子")
    assert "baseline" not in text
    assert "numeric_conflict" not in text
