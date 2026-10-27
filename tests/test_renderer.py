"""渲染规则：长度上限、整块取舍、降级文案、免责声明（§7 / §9 / §15）。"""

from __future__ import annotations

import re

import pytest

from archive import notices, renderer
from archive.models import CharacterProfile

HANGUL = re.compile(r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]")
BULLET = "· "


def _profile(service, term, scope=("skill", "weapon")) -> CharacterProfile:
    result = service._resolve(term)[0]
    assert result.character is not None
    return service._profile(result.character, scope)


# ---------------------------------------------------------------- 长度


@pytest.mark.parametrize("term", ["汐见琴音", "主角", "雨宫莲·星舞"])
@pytest.mark.parametrize("command", ["character_message", "skills_message", "weapons_message"])
def test_message_within_limit(service, term, command):
    text = getattr(service, command)(term)
    assert len(text) <= service.max_chars


@pytest.mark.parametrize("limit", [400, 600, 900])
def test_limit_is_respected_at_any_config(db_path, limit):
    from archive.service import ArchiveService

    svc = ArchiveService(db_path, max_chars=limit)
    try:
        for command in ("character_message", "skills_message", "weapons_message"):
            assert len(getattr(svc, command)("汐见琴音")) <= limit
    finally:
        svc.close()


# ---------------------------------------------------------------- 必留内容


@pytest.mark.parametrize("command", ["character_message", "skills_message", "weapons_message"])
def test_disclaimer_always_present(service, command):
    assert notices.DISCLAIMER in getattr(service, command)("汐见琴音")


@pytest.mark.parametrize("command", ["character_message", "skills_message", "weapons_message"])
def test_no_hangul_leaks(service, command):
    assert not HANGUL.search(getattr(service, command)("汐见琴音"))


def test_source_line_present(service):
    assert "来源：Lufel 社区资料" in service.character_message("汐见琴音")


def _violates(text: str, phrase: str) -> bool:
    """检出违规说法，但放行「非官方数据库」这类否定句。"""
    start = 0
    while True:
        index = text.find(phrase, start)
        if index == -1:
            return False
        prefix = text[max(0, index - 3):index]
        if not (prefix.endswith("非") or prefix.endswith("不是") or prefix.endswith("并非")):
            return True
        start = index + len(phrase)


def test_forbidden_phrases_absent(service):
    texts = [
        service.character_message("汐见琴音"),
        service.skills_message("汐见琴音"),
        service.weapons_message("汐见琴音"),
        service.character_list_message(),
        renderer.render_help(),
    ]
    for text in texts:
        for phrase in notices.FORBIDDEN_PHRASES:
            assert not _violates(text, phrase), f"出现违规说法：{phrase}"


# ---------------------------------------------------------------- 降级文案


def test_missing_weapon_name_degrades(service):
    """主角的两把武器在社区数据里没有名称，必须降级而不是编一个。"""
    text = service.weapons_message("主角")
    assert "未收录名称（weapon5-1）" in text
    assert "未收录名称（weapon4-1）" in text


def test_missing_weapon_skill_name_degrades(service):
    text = service.weapons_message("汐见琴音")
    assert notices.MISSING_WEAPON_SKILL_NAME in text


def test_needs_review_badge(service):
    assert notices.NEEDS_REVIEW_BADGE in service.character_message("主角")


# ---------------------------------------------------------------- 截断计数


def _bullets(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith(BULLET))


def _truncated_count(text: str) -> int:
    match = re.search(r"另有 (\d+) 条未显示", text)
    return int(match.group(1)) if match else 0


@pytest.mark.parametrize("term", ["汐见琴音", "雨宫莲·星舞", "主角"])
def test_skill_truncation_count_matches(service, term):
    profile = _profile(service, term, ("skill",))
    text = service.skills_message(term)
    assert _bullets(text) + _truncated_count(text) == len(profile.skills)


@pytest.mark.parametrize("term", ["汐见琴音", "雨宫莲·星舞", "主角"])
def test_weapon_truncation_count_matches(service, term):
    profile = _profile(service, term, ("weapon",))
    text = service.weapons_message(term)
    assert _bullets(text) + _truncated_count(text) == len(profile.weapons)


def test_no_truncation_line_when_everything_fits(service):
    """不截断时不许出现「另有 0 条」。"""
    text = service.skills_message("汐见琴音")
    if _truncated_count(text) == 0:
        assert "另有 0 条" not in text


# ---------------------------------------------------------------- 断句


def test_clip_keeps_sentence_boundary():
    text = "第一句话。" * 40
    clipped = renderer._clip_sentence(text, 30)
    assert clipped.endswith("…")
    assert clipped[:-1].endswith("。")


def test_clip_returns_full_text_without_sentence_end():
    """没有句末标点时整段保留，绝不硬切。"""
    text = "无标点的长文本" * 30
    assert renderer._clip_sentence(text, 30) == text


def test_clip_short_text_untouched():
    assert renderer._clip_sentence("短句。", 100) == "短句。"


def test_no_half_sentence_in_skill_body(service):
    """技能正文每行要么完整，要么以省略号收尾。"""
    text = service.skills_message("汐见琴音")
    for line in text.splitlines():
        if not line.startswith("  "):
            continue
        stripped = line.strip()
        if stripped.startswith(("生命", "技能名称", "未收录")):
            continue
        if stripped.endswith("…"):
            continue
        # 完整句子的正文应当以句末标点或数字/括号收尾
        assert not stripped.endswith("，"), f"疑似半句话：{stripped[-20:]}"


# ---------------------------------------------------------------- 非数据回复


def test_usage_message():
    text = renderer.render_usage("/p5x角色", "汐见琴音")
    assert text.startswith("用法：/p5x角色 <角色名称>")
    assert "例如：/p5x角色 汐见琴音" in text


def test_not_found_message(service):
    text = service.character_message("不存在角色XYZ")
    assert "没有找到“不存在角色XYZ”对应的角色。" in text
    assert "/p5x角色列表" in text


def test_multiple_message(service):
    text = service.character_message("琴")
    assert text.startswith("找到多个可能的角色：")
    assert "请使用更完整的名称重新查询。" in text


def test_help_message():
    text = renderer.render_help()
    assert "【夜幕档案】" in text
    assert "/p5x角色 <名称>" in text
    assert notices.HELP_SOURCE_NOTE in text


def test_character_list_within_limit(service):
    text = service.character_list_message()
    assert len(text) <= service.max_chars
    assert "当前收录 72 名角色" in text
