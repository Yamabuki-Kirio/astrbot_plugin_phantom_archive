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
class LingQianDraw:
    """一支观音灵签。签诗原文里带换行，展示时按行拆。"""

    number: int = 0
    grade: str = ""
    poem: str = ""
    explain: str = ""

    @property
    def poem_lines(self) -> tuple[str, ...]:
        return tuple(line.strip() for line in self.poem.splitlines() if line.strip())

    @property
    def label(self) -> str:
        if self.number <= 0:
            return self.grade
        return f"第 {self.number} 签 · {self.grade}"

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "grade": self.grade,
            "poem": self.poem,
            "explain": self.explain,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "LingQianDraw":
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            number=_int(raw.get("number")),
            grade=_text(raw.get("grade")),
            poem=_text(raw.get("poem")),
            explain=_text(raw.get("explain")),
        )


@dataclass(frozen=True)
class ZhouyiDraw:
    """一卦。变卦信息只在有动爻时存在。"""

    number: int = 0
    name: str = ""
    symbol: str = ""
    upper: str = ""
    lower: str = ""
    guaci: str = ""
    jie: str = ""
    moving: tuple[int, ...] = ()
    changed_number: int | None = None
    changed_name: str = ""

    @property
    def label(self) -> str:
        """「坤卦」。不带 Unicode 卦符 —— 多数中文字体没有 U+4DC0–4DFF，
        在无头 Chrome 里会渲染成豆腐块，卡片上宁可少一个装饰。"""
        if not self.name:
            return ""
        return f"{self.name}卦"

    @property
    def label_with_symbol(self) -> str:
        """带卦符的版本，只给确定字体支持的地方用（例如终端自检）。"""
        return f"{self.symbol} {self.label}".strip()

    @property
    def trigram_label(self) -> str:
        if not (self.upper and self.lower):
            return ""
        return f"{self.upper}上{self.lower}下"

    @property
    def moving_label(self) -> str:
        """「第3、5爻」。调用方自己加「动爻：」前缀，这里不重复。"""
        if not self.moving:
            return ""
        return "第" + "、".join(str(i) for i in self.moving) + "爻"

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "symbol": self.symbol,
            "upper": self.upper,
            "lower": self.lower,
            "guaci": self.guaci,
            "jie": self.jie,
            "moving": list(self.moving),
            "changed_number": self.changed_number,
            "changed_name": self.changed_name,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ZhouyiDraw":
        if not isinstance(raw, Mapping):
            return cls()
        moving = raw.get("moving")
        changed = raw.get("changed_number")
        return cls(
            number=_int(raw.get("number")),
            name=_text(raw.get("name")),
            symbol=_text(raw.get("symbol")),
            upper=_text(raw.get("upper")),
            lower=_text(raw.get("lower")),
            guaci=_text(raw.get("guaci")),
            jie=_text(raw.get("jie")),
            moving=tuple(_int(item) for item in moving) if isinstance(moving, (list, tuple)) else (),
            changed_number=_int(changed) if changed is not None else None,
            changed_name=_text(raw.get("changed_name")),
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
    lingqian: LingQianDraw = field(default_factory=LingQianDraw)
    zhouyi: ZhouyiDraw = field(default_factory=ZhouyiDraw)
    ganzhi: str = ""
    advice: str = ""
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

    def to_payload(self) -> dict:
        """存进 ``fortune_json`` 的那一份。不含身份字段，身份走表列。"""
        return {
            "character": self.character.to_dict(),
            "tarot": self.tarot.to_dict(),
            "lingqian": self.lingqian.to_dict(),
            "zhouyi": self.zhouyi.to_dict(),
            "ganzhi": self.ganzhi,
            "advice": self.advice,
        }

    @classmethod
    def from_payload(cls, raw: Any, **identity: str) -> "DailyFortune":
        """从库里读回来。``identity`` 覆盖 date_key / timezone / platform_id / user_id / display_name。"""
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            character=DailyCharacter.from_dict(data.get("character")),
            tarot=TarotDraw.from_dict(data.get("tarot")),
            lingqian=LingQianDraw.from_dict(data.get("lingqian")),
            zhouyi=ZhouyiDraw.from_dict(data.get("zhouyi")),
            ganzhi=_text(data.get("ganzhi")),
            advice=_text(data.get("advice")),
            stored=True,
            **identity,
        )


__all__ = [
    "DailyCharacter",
    "DailyFortune",
    "LingQianDraw",
    "TarotDraw",
    "ZhouyiDraw",
    "TAROT_UPRIGHT",
    "TAROT_REVERSED",
    "UNKNOWN_CHARACTER_NAME",
]
