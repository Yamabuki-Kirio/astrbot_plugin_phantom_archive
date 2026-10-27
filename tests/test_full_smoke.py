"""72 名角色全量冒烟：一次跑完三条指令，检查长度、声明、冲突准确性（§15）。"""

from __future__ import annotations

import re

import pytest

from archive import notices

HANGUL = re.compile(r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]")

COMMANDS = ("character_message", "skills_message", "weapons_message")
GUIDE_TYPES = ("review", "setting", "operation", "party", "ritual", "innate")


@pytest.fixture(scope="module")
def characters(repository):
    return repository.list_characters()


def test_character_count(characters):
    assert len(characters) == 72


def test_all_characters_render(service, characters):
    """任何一条都不能抛异常，且都必须带免责声明、不含韩文、不超长。"""
    for character in characters:
        for command in COMMANDS:
            text = getattr(service, command)(character.source_key)
            assert text, f"{character.name_zh}/{command} 返回空"
            assert notices.DISCLAIMER in text, f"{character.name_zh}/{command} 缺免责声明"
            assert not HANGUL.search(text), f"{character.name_zh}/{command} 含韩文"
            assert len(text) <= service.max_chars, (
                f"{character.name_zh}/{command} 长度 {len(text)} 超过 {service.max_chars}"
            )


def test_characters_with_skills_render(service, characters, repository):
    with_skills = [c for c in characters if repository.skills(c.source_key)]
    assert len(with_skills) == 68
    for character in with_skills:
        text = service.skills_message(character.source_key)
        assert "■" not in text  # 技能页不应出现分区符号
        assert "条" in text


def test_characters_with_weapons_render(service, characters, repository):
    with_weapons = [c for c in characters if repository.weapons(c.source_key)]
    assert len(with_weapons) == 69
    for character in with_weapons:
        text = service.weapons_message(character.source_key)
        assert "技能名称" in text or notices.MISSING_WEAPON_SKILL_NAME in text


def test_conflict_characters_count(service, characters, repository):
    """技能 + 武器范围下应为 33 名；全类型是 55 名（含评价与意识）。"""
    from archive.repository import CONFLICT_SCOPE_CHARACTER

    scoped = [c for c in characters if repository.conflict_count(c.source_key, CONFLICT_SCOPE_CHARACTER) > 0]
    assert len(scoped) == 33

    all_types = {
        r["character_key"]
        for r in repository._db.fetch_all("SELECT DISTINCT character_key FROM numeric_conflicts")
    }
    assert len(all_types) == 55

    for character in scoped:
        assert notices.NUMERIC_CONFLICT_WARNING in service.character_message(character.source_key)


def test_max_message_length(service, characters):
    longest = 0
    for character in characters:
        for command in COMMANDS:
            longest = max(longest, len(getattr(service, command)(character.source_key)))
    assert longest <= service.max_chars
    print(f"\n最长消息 {longest} 字（上限 {service.max_chars}）")


def test_no_unhandled_exception_on_odd_terms(service):
    for term in ["", "   ", "·", "%", "'; DROP TABLE zh_characters; --", "🙂", "A" * 200]:
        for command in COMMANDS:
            text = getattr(service, command)(term)
            assert isinstance(text, str) and text


def test_all_guide_queries_render(service, characters):
    for character in characters:
        for guide_type in GUIDE_TYPES:
            text = service.guide_message(character.source_key, guide_type)
            assert text
            assert notices.DISCLAIMER in text
            assert len(text) <= service.max_chars
        text = service.all_guides_message(character.source_key)
        assert notices.DISCLAIMER in text
        assert len(text) <= service.max_chars
