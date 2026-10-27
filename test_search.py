"""检索规则：完全匹配优先、四种别名、无结果、多候选、空输入（§8 / §15）。"""

from __future__ import annotations

import pytest

from archive.search import MAX_CANDIDATES, normalize_key, normalize_term, search_characters

ALIASES = ["汐见琴音", "코토네", "KOTONE", "kotone"]


@pytest.mark.parametrize("term", ALIASES)
def test_aliases_hit_same_character(repository, term):
    result = search_characters(repository, term)
    assert result.kind == "one"
    assert result.character.source_key == "코토네"


@pytest.mark.parametrize("term", ["  汐见琴音  ", "汐见琴音\n", " 코토네 "])
def test_whitespace_is_trimmed(repository, term):
    result = search_characters(repository, term)
    assert result.kind == "one"
    assert result.character.source_key == "코토네"


def test_codename_is_case_insensitive(repository):
    upper = search_characters(repository, "KOTONE")
    lower = search_characters(repository, "kotone")
    mixed = search_characters(repository, "KoTone")
    assert {upper.character.source_key, lower.character.source_key, mixed.character.source_key} == {"코토네"}


def test_exact_beats_fuzzy(repository):
    """「雨宫莲」必须命中 1 条，而不是把「雨宫莲·星舞」也算进来。"""
    result = search_characters(repository, "雨宫莲")
    assert result.kind == "one"
    assert result.character.name_zh == "雨宫莲"


@pytest.mark.parametrize("term", ["雨宫莲星舞", "雨宫莲-星舞", "雨宫莲・星舞"])
def test_variant_name_ignores_decorative_separator(repository, term):
    result = search_characters(repository, term)
    assert result.kind == "one"
    assert result.character.name_zh == "雨宫莲·星舞"


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("周星驰", "雨宫莲·星舞"),
        ("哈姆子", "汐见琴音"),
        ("P3P女主", "汐见琴音"),
        ("鬼太郎", "结城理"),
        ("阿鬼", "结城理"),
        ("牢理", "结城理"),
    ],
)
def test_community_aliases(repository, term, expected):
    result = search_characters(repository, term)
    assert result.kind == "one"
    assert result.character.name_zh == expected


def test_one_character_typo_is_tolerated(repository):
    result = search_characters(repository, "雨宫连")
    assert result.kind == "one"
    assert result.character.name_zh == "雨宫莲"


def test_multiple_candidates(repository):
    result = search_characters(repository, "琴")
    assert result.kind == "many"
    assert 2 <= len(result.candidates) <= MAX_CANDIDATES
    names = [c.name_zh for c in result.candidates]
    assert "汐见琴音" in names


def test_candidates_capped(repository):
    result = search_characters(repository, "·")
    assert len(result.candidates) <= MAX_CANDIDATES


def test_not_found(repository):
    result = search_characters(repository, "不存在角色XYZ")
    assert result.kind == "none"
    assert result.character is None
    assert result.candidates == ()


@pytest.mark.parametrize("term", ["", "   ", None])
def test_empty_term(repository, term):
    result = search_characters(repository, term)
    assert result.kind == "none"
    assert result.term == ""


def test_normalize_term():
    assert normalize_term("  a   b  ") == "a b"
    assert normalize_term(None) == ""
    assert normalize_term("汐见琴音") == "汐见琴音"


def test_normalize_key():
    assert normalize_key(" 雨宫莲・星舞 ") == "雨宫莲星舞"
    assert normalize_key("JOKER-Starlight") == "jokerstarlight"
