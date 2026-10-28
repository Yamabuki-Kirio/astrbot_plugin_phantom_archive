"""每日运势的数据结构。

这一层只有纯数据：不含随机、不含 IO、不含排版。
随机在 :mod:`engine`，持久化在 :mod:`repository`，排版在 :mod:`presenter`，
三者都只依赖这里的类型，互相之间不 import —— 这样任何一层坏掉都不会连带。

所有类型都提供 ``to_dict`` / ``from_dict``，因为每日库要把整份结果序列化存下来：
次日再看同一天的记录时，必须原样还原，而不是重新抽一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from . import fortune_grade

TAROT_UPRIGHT = "正位"
TAROT_REVERSED = "逆位"

# 缺字段时的占位，禁止凭空编一个名字出来
UNKNOWN_CHARACTER_NAME = "未知角色"


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class TarotDraw:
    """一张塔罗。"""

    name: str
    is_reversed: bool = False
    meaning: str = ""

    @property
    def position(self) -> str:
        return TAROT_REVERSED if self.is_reversed else TAROT_UPRIGHT

    @property
    def label(self) -> str:
        return f"{self.name}（{self.position}）"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "is_reversed": self.is_reversed,
            "meaning": self.meaning,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TarotDraw":
        if not isinstance(raw, Mapping):
            return cls(name="", meaning="")
        return cls(
            name=_text(raw.get("name")),
            is_reversed=bool(raw.get("is_reversed")),
            meaning=_text(raw.get("meaning")),
        )


@dataclass(frozen=True)
class DailyCharacter:
    """今日角色。key 用 ``source_key``：稳定、唯一、与数据库物理行序无关。"""

    key: str = ""
    slug: str = ""
    name: str = UNKNOWN_CHARACTER_NAME
    codename: str = ""
    element: str = ""
    position: str = ""
    rarity: int = 0

    @property
    def subtitle(self) -> str:
        parts: list[str] = []
        if self.rarity:
            parts.append(f"{self.rarity}★")
        if self.element:
            parts.append(f"{self.element}属性")
        if self.position:
            parts.append(self.position)
        return "｜".join(parts)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "slug": self.slug,
            "name": self.name,
            "codename": self.codename,
            "element": self.element,
            "position": self.position,
            "rarity": self.rarity,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "DailyCharacter":
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            key=_text(raw.get("key")),
            slug=_text(raw.get("slug")),
            name=_text(raw.get("name")) or UNKNOWN_CHARACTER_NAME,
            codename=_text(raw.get("codename")),
            element=_text(raw.get("element")),
            position=_text(raw.get("position")),
            rarity=_int(raw.get("rarity")),
        )


@dataclass(frozen=True)
class DailyFortune:
    """一份完整的每日运势。文字和图片都由这一份对象生成，不各自再抽。"""

    date_key: str = ""
    timezone: str = ""
    platform_id: str = ""
    user_id: str = ""
    display_name: str = ""
    character: DailyCharacter = field(default_factory=DailyCharacter)
    tarot: TarotDraw = field(default_factory=TarotDraw)
    ganzhi: str = ""
    stored: bool = False  # True = 这条是当天之前就已经存下来的

    @property
    def date_label(self) -> str:
        """2026-10-27 → 2026 年 10 月 27 日。拆不出来就原样返回。"""
        parts = self.date_key.split("-")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return self.date_key
        return f"{parts[0]} 年 {int(parts[1])} 月 {int(parts[2])} 日"

    @property
    def day_label(self) -> str:
        return f"{self.date_label} · {self.ganzhi}日" if self.ganzhi else self.date_label

    @property
    def character_key(self) -> str:
        return self.character.key

    @property
    def grade(self) -> str:
        """塔罗吉凶评级（大吉 / 吉 / 平 / 凶）。

        由牌名与正逆位实时算出，不进 payload：
        旧记录没有这个字段也能拿到同一结果，改了规则也不用洗库。
        """
        return fortune_grade.grade_of(self.tarot.name, self.tarot.is_reversed)

    @property
    def guide(self) -> tuple[str, str]:
        """与评级对应的 ``(宜, 忌)``。"""
        return fortune_grade.guide_of(self.grade)

    @property
    def advice(self) -> str:
        """综合建议：由「牌名 + 正逆位 + 牌义 + 评级」拼出的确定性文案。

        同样是实时算出、不进 payload —— 旧记录也能拿到同一段话，
        改了文案不用洗库，也不会因为存档里的旧句子和当前牌义对不上。
        """
        return fortune_grade.build_advice(
            self.tarot.name,
            self.tarot.meaning,
            self.tarot.is_reversed,
            grade=self.grade,
        )

    def to_payload(self) -> dict:
        """存进 ``fortune_json`` 的那一份。不含身份字段，身份走表列。"""
        return {
            "character": self.character.to_dict(),
            "tarot": self.tarot.to_dict(),
            "ganzhi": self.ganzhi,
        }

    @classmethod
    def from_payload(cls, raw: Any, **identity: str) -> "DailyFortune":
        """从库里读回来。

        ``identity`` 覆盖 date_key / timezone / platform_id / user_id / display_name。
        旧记录里多出来的 ``lingqian`` / ``zhouyi`` / ``advice`` 字段会被忽略，
        已停用的板块不会因为历史数据又冒出来。
        """
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            character=DailyCharacter.from_dict(data.get("character")),
            tarot=TarotDraw.from_dict(data.get("tarot")),
            ganzhi=_text(data.get("ganzhi")),
            stored=True,
            **identity,
        )


__all__ = [
    "DailyCharacter",
    "DailyFortune",
    "TarotDraw",
    "TAROT_UPRIGHT",
    "TAROT_REVERSED",
    "UNKNOWN_CHARACTER_NAME",
]
