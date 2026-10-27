"""今日角色抽取：确定性、与数据库物理行序无关。

三条硬约束，任何一条破了都会让「同一天结果固定」失效：

1. 候选池**必须**按稳定键 ``source_key`` 排序后再抽 —— 直接按
   ``SELECT *`` 的返回顺序抽，库被 VACUUM、加了索引、换了查询计划，
   同一个人同一天就会抽到不同角色。
2. 不允许用 ``random.random()`` / ``random.choice()``，随机源只能是
   调用方传进来的种子字节；本模块自己**不产生**任何随机性。
3. 索引取模用 SHA-256 派生的整数，不用 ``hash()``（Python 的字符串哈希
   带进程级盐，重启就变）。
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Sequence

from .models import DailyCharacter


def stable_index(seed: bytes, size: int) -> int:
    """把种子映射到 ``[0, size)``。取 SHA-256 前 8 字节，够均匀也不会溢出。"""
    if size <= 0:
        raise ValueError("候选池为空，无法抽取")
    digest = hashlib.sha256(seed).digest()
    return int.from_bytes(digest[:8], "big") % size


def sort_key(character: DailyCharacter) -> tuple[str, str, str]:
    return (character.key or "", character.slug or "", character.name or "")


def candidates(characters: Iterable[DailyCharacter]) -> tuple[DailyCharacter, ...]:
    """去重 + 按 source_key 排序。

    去重键用 ``key``（source_key）：``YUI`` 与 ``YUI·星舞`` 的 key 不同，
    是两个角色，各自独立参与抽取；同一个 key 出现多次只算一次。
    """
    unique: dict[str, DailyCharacter] = {}
    for character in characters:
        if not character.key or character.key in unique:
            continue
        unique[character.key] = character
    return tuple(unique[key] for key in sorted(unique))


def draw(characters: Sequence[DailyCharacter], seed: bytes) -> DailyCharacter:
    """从候选池抽一个。池子必须已经排序（本函数会再排一次，幂等）。"""
    pool = candidates(characters)
    if not pool:
        raise ValueError("候选池为空，无法抽取")
    return pool[stable_index(seed, len(pool))]


def from_character(character) -> DailyCharacter:
    """把资料库的 ``Character`` 映射成抽取用的 :class:`DailyCharacter`。

    用 getattr 而不是直接 import ``archive.models``：这一层只关心
    「有 source_key / name_zh / slug / element_zh / position_zh / rarity」，
    测试传个鸭子类型也应当能过。
    """
    def text(name: str) -> str:
        value = getattr(character, name, "")
        return value.strip() if isinstance(value, str) else ""

    rarity = getattr(character, "rarity", 0)
    try:
        rarity_value = int(rarity) if rarity is not None else 0
    except (TypeError, ValueError):
        rarity_value = 0

    key = text("source_key")
    return DailyCharacter(
        key=key,
        slug=text("slug"),
        name=text("name_zh") or key or "未知角色",
        codename=text("codename_zh"),
        element=text("element_zh"),
        position=text("position_zh"),
        rarity=rarity_value,
    )


__all__ = ["candidates", "draw", "from_character", "sort_key", "stable_index"]
