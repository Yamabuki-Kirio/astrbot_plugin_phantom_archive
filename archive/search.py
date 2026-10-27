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

from .models import Character, SearchResult
from .repository import ArchiveRepository

MAX_CANDIDATES = 10


def normalize_term(term: str | None) -> str:
    """去掉首尾空白并把连续空白压成一个空格。"""
    if not term:
        return ""
    return " ".join(str(term).split())


def _dedupe(items: tuple[Character, ...]) -> tuple[Character, ...]:
    seen: set[str] = set()
    ordered: list[Character] = []
    for item in items:
        if item.source_key in seen:
            continue
        seen.add(item.source_key)
        ordered.append(item)
    return tuple(ordered)


def search_characters(
    repo: ArchiveRepository,
    term: str | None,
    limit: int = MAX_CANDIDATES,
) -> SearchResult:
    normalized = normalize_term(term)
    if not normalized:
        return SearchResult(character=None, candidates=(), term="")

    tiers = (
        repo.characters_by_name,
        repo.characters_by_source_key,
        repo.characters_by_slug,
        repo.characters_by_codename,
        repo.characters_by_name_like,
        repo.characters_by_any_like,
    )

    for lookup in tiers:
        matches = _dedupe(lookup(normalized, limit))
        if not matches:
            continue
        if len(matches) == 1:
            return SearchResult(character=matches[0], candidates=matches, term=normalized)
        return SearchResult(character=None, candidates=matches[:limit], term=normalized)

    return SearchResult(character=None, candidates=(), term=normalized)
