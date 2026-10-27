"""正传角色与大阿卡纳的对应关系。

这里只收录已经进入当前 P5X 角色库的 P3/P5 角色。映射采用角色的
Persona/Confidant 阿卡纳；Faith、Aeon 等不在当前 22 张韦特大阿卡纳牌组中的
牌不会硬映射到别的牌。
"""

from __future__ import annotations

from typing import Iterable

from .models import DailyCharacter, TarotDraw


# 使用 source_key，避免中文翻译或昵称变化破坏匹配。同一人的特殊形态也保留。
ARCANA_CHARACTER_KEYS: dict[str, frozenset[str]] = {
    "愚者": frozenset({"원더", "렌", "렌·댄싱 스타", "유키 마코토", "코토네"}),
    "魔术师": frozenset({"모르가나", "모르가나·댄싱 스타", "준페이"}),
    "女祭司": frozenset({"마코토", "후카"}),
    "皇后": frozenset({"하루", "미츠루"}),
    "皇帝": frozenset({"유스케", "사나다"}),
    "教皇": frozenset({"아라가키"}),
    "恋人": frozenset({"안", "안·댄싱 스타", "유카리"}),
    "战车": frozenset({"류지", "류지·댄싱 스타", "아이기스"}),
    "力量": frozenset({"코로마루", "J&C"}),
    "隐士": frozenset({"후타바"}),
    "正义": frozenset({"아케치", "아마다"}),
}


def arcana_name(card: TarotDraw | str) -> str:
    """从 ``VIII 力量 Strength`` 这种展示名中取中文牌名。"""
    raw = card.name if isinstance(card, TarotDraw) else str(card)
    parts = raw.split()
    return parts[1] if len(parts) >= 2 else raw.strip()


def preferred_candidates(
    card: TarotDraw | str,
    characters: Iterable[DailyCharacter],
) -> tuple[DailyCharacter, ...]:
    keys = ARCANA_CHARACTER_KEYS.get(arcana_name(card), frozenset())
    if not keys:
        return ()
    return tuple(character for character in characters if character.key in keys)


__all__ = ["ARCANA_CHARACTER_KEYS", "arcana_name", "preferred_candidates"]
