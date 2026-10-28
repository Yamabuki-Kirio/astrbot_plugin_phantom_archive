"""角色检索：严格按「完全匹配优先于模糊匹配」的分级顺序（§8）。

优先级：
1. 中文名完全匹配
2. 原名（韩文内部标识）完全匹配
3. slug 完全匹配
4. 代号完全匹配（不区分大小写）
5. 中文名模糊匹配
6. 其他字段模糊匹配

一旦某一级有结果就停止，不再往下找 —— 否则「雨宫莲」会被
「雨宫莲·星舞」的模糊结果污染，变成多候选。
"""

from __future__ import annotations

import unicodedata

from .character_aliases import CHARACTER_ALIASES
from .models import Character, SearchResult
from .repository import ArchiveRepository

MAX_CANDIDATES = 10

# 玩家输入角色特殊形态时通常不会输入这些装饰性分隔符。例如数据库中的
# “雨宫莲·星舞”，实际输入更常见的是“雨宫莲星舞”或“雨宫莲-星舞”。
_IGNORED_SEPARATORS = frozenset(" \t\r\n·•・‧．.－—–-_・")


def normalize_term(term: str | None) -> str:
    """去掉首尾空白并把连续空白压成一个空格。"""
    if not term:
        return ""
    return " ".join(str(term).split())


def normalize_key(term: str | None) -> str:
    """生成用于宽松比较的键：兼容全半角、大小写及装饰性分隔符。"""
    normalized = unicodedata.normalize("NFKC", normalize_term(term)).casefold()
    return "".join(char for char in normalized if char not in _IGNORED_SEPARATORS)


def _dedupe(items: tuple[Character, ...]) -> tuple[Character, ...]:
    seen: set[str] = set()
    ordered: list[Character] = []
    for item in items:
        if item.source_key in seen:
            continue
        seen.add(item.source_key)
        ordered.append(item)
    return tuple(ordered)


def _identities(character: Character) -> tuple[str, ...]:
    """返回用户可能输入的所有数据库身份字段。"""
    return tuple(
        value
        for value in (
            character.name_zh,
            character.source_key,
            character.slug,
            character.codename_zh,
        )
        if value
    )


def _characters_by_normalized_exact(
    characters: tuple[Character, ...], term_key: str
) -> tuple[Character, ...]:
    return _dedupe(
        tuple(
            character
            for character in characters
            if any(normalize_key(value) == term_key for value in _identities(character))
        )
    )


def _characters_by_alias(
    characters: tuple[Character, ...], term_key: str
) -> tuple[Character, ...]:
    aliases = {normalize_key(alias): target for alias, target in CHARACTER_ALIASES.items()}
    target = aliases.get(term_key)
    if not target:
        return ()
    return tuple(character for character in characters if character.name_zh == target)


def _characters_by_normalized_contains(
    characters: tuple[Character, ...], term_key: str
) -> tuple[Character, ...]:
    return _dedupe(
        tuple(
            character
            for character in characters
            if any(term_key in normalize_key(value) for value in _identities(character))
        )
    )


def _edit_distance(left: str, right: str) -> int:
    """小规模 Levenshtein 距离；角色表很小，无需引入第三方依赖。"""
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, right_char in enumerate(right, 1):
        current = [row]
        for column, left_char in enumerate(left, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _characters_by_typo(
    characters: tuple[Character, ...], term_key: str
) -> tuple[Character, ...]:
    """容忍一个错字，但只接受唯一最佳距离，避免过度猜测短输入。"""
    if len(term_key) < 3:
        return ()

    scored: list[tuple[int, Character]] = []
    for character in characters:
        keys = tuple(normalize_key(value) for value in _identities(character))
        distances = [_edit_distance(term_key, key) for key in keys if key]
        if distances:
            scored.append((min(distances), character))
    if not scored:
        return ()

    best = min(score for score, _ in scored)
    max_distance = 1 if len(term_key) < 8 else 2
    if best > max_distance:
        return ()
    return _dedupe(tuple(character for score, character in scored if score == best))


def _result(matches: tuple[Character, ...], term: str, limit: int) -> SearchResult:
    matches = _dedupe(matches)[:limit]
    if len(matches) == 1:
        return SearchResult(character=matches[0], candidates=matches, term=term)
    return SearchResult(character=None, candidates=matches, term=term)


def search_characters(
    repo: ArchiveRepository,
    term: str | None,
    limit: int = MAX_CANDIDATES,
) -> SearchResult:
    normalized = normalize_term(term)
    if not normalized:
        return SearchResult(character=None, candidates=(), term="")

    exact_tiers = (
        repo.characters_by_name,
        repo.characters_by_source_key,
        repo.characters_by_slug,
        repo.characters_by_codename,
    )

    for lookup in exact_tiers:
        matches = _dedupe(lookup(normalized, limit))
        if not matches:
            continue
        return _result(matches, normalized, limit)

    # 后续匹配需要一次读取完整角色表。当前数据库规模很小（几十名角色），换来
    # SQLite 无法直接提供的 Unicode/标点归一化，以及可测试的确定性排序。
    characters = repo.list_characters()
    term_key = normalize_key(normalized)

    tiers = (
        _characters_by_normalized_exact(characters, term_key),
        _characters_by_alias(characters, term_key),
        _characters_by_normalized_contains(characters, term_key),
        _characters_by_typo(characters, term_key),
    )
    for matches in tiers:
        if matches:
            return _result(matches, normalized, limit)

    return SearchResult(character=None, candidates=(), term=normalized)
