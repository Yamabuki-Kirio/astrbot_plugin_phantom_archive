"""数据库行的轻量数据模型。

只做「列名 → 属性」的映射，不做任何翻译、猜测或补全。
字段缺失时保留 None，由渲染器决定怎么降级显示。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


@dataclass(frozen=True)
class Character:
    source_key: str
    name_zh: str | None
    codename_zh: str | None
    slug: str | None
    element_zh: str | None
    position_zh: str | None
    rarity: int | None
    description_zh: str | None
    source_status: str | None
    translation_status: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Character":
        return cls(
            source_key=_get(row, "source_key", ""),
            name_zh=_get(row, "name_zh"),
            codename_zh=_get(row, "codename_zh"),
            slug=_get(row, "slug"),
            element_zh=_get(row, "element_zh"),
            position_zh=_get(row, "position_zh"),
            rarity=_get(row, "rarity"),
            description_zh=_get(row, "description_zh"),
            source_status=_get(row, "source_status"),
            translation_status=_get(row, "translation_status"),
        )

    @property
    def display_name(self) -> str:
        """没有中文名时退回内部标识，绝不臆造译名。"""
        return self.name_zh or self.source_key


@dataclass(frozen=True)
class Skill:
    character_key: str
    skill_key: str
    name_zh: str | None
    element_zh: str | None
    type_zh: str | None
    description_zh: str | None
    sp: int | None
    cooldown: int | None
    translation_status: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Skill":
        return cls(
            character_key=_get(row, "character_key", ""),
            skill_key=_get(row, "skill_key", ""),
            name_zh=_get(row, "name_zh"),
            element_zh=_get(row, "element_zh"),
            type_zh=_get(row, "type_zh"),
            description_zh=_get(row, "description_zh"),
            sp=_get(row, "sp"),
            cooldown=_get(row, "cooldown"),
            translation_status=_get(row, "translation_status"),
        )

    @property
    def category(self) -> str | None:
        """技能分类优先取 type_zh，被动/增益这类落在 element_zh 上。"""
        return self.type_zh or self.element_zh


@dataclass(frozen=True)
class Weapon:
    character_key: str
    weapon_key: str
    name_zh: str | None
    rarity: int | None
    health: float | None
    attack: float | None
    defense: float | None
    skill_name_zh: str | None
    description_zh: str | None
    translation_status: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Weapon":
        return cls(
            character_key=_get(row, "character_key", ""),
            weapon_key=_get(row, "weapon_key", ""),
            name_zh=_get(row, "name_zh"),
            rarity=_get(row, "rarity"),
            health=_get(row, "health"),
            attack=_get(row, "attack"),
            defense=_get(row, "defense"),
            skill_name_zh=_get(row, "skill_name_zh"),
            description_zh=_get(row, "description_zh"),
            translation_status=_get(row, "translation_status"),
        )


@dataclass(frozen=True)
class Guide:
    character_key: str
    guide_type: str
    content: dict[str, Any]
    source_status: str | None
    translation_status: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Guide":
        raw = _get(row, "content_json", "{}")
        try:
            content = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (json.JSONDecodeError, TypeError, ValueError):
            content = {}
        return cls(
            character_key=_get(row, "character_key", ""),
            guide_type=_get(row, "guide_type", ""),
            content=content if isinstance(content, dict) else {},
            source_status=_get(row, "source_status"),
            translation_status=_get(row, "translation_status"),
        )


@dataclass(frozen=True)
class GuideProfile:
    character: Character
    guides: tuple[Guide, ...]
    conflict_count: int
    conflict_scope: str

    @property
    def has_conflict(self) -> bool:
        return self.conflict_count > 0


@dataclass(frozen=True)
class CharacterProfile:
    """一次查询需要的全部数据，不再二次回库。"""

    character: Character
    skills: tuple[Skill, ...]
    weapons: tuple[Weapon, ...]
    conflict_count: int
    conflict_scope: str

    @property
    def has_conflict(self) -> bool:
        return self.conflict_count > 0


@dataclass(frozen=True)
class SearchResult:
    """搜索结果三态：唯一命中 / 多个候选 / 没有结果。"""

    character: Character | None
    candidates: tuple[Character, ...]
    term: str

    @property
    def kind(self) -> str:
        if self.character is not None:
            return "one"
        return "many" if self.candidates else "none"
