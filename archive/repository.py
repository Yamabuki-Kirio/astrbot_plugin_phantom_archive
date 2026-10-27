"""数据读取层：只负责把 SQL 结果变成模型，不含任何排版逻辑。

所有 SQL 都是参数化的（§13），没有一处字符串拼接。
"""

from __future__ import annotations

from typing import Sequence

from .database import ArchiveDatabase
from .models import Character, CharacterProfile, Guide, GuideProfile, Skill, Weapon

# 数值冲突的作用域（§12）：
# 角色查询汇总「技能 + 武器」，技能查询只看技能，武器查询只看武器。
# 意识（ritual）与角色评价（review）本阶段不对外展示，因此不计入。
#
# 两个数字含义不同，别混用：
#   角色总览冲突角色 33 名 —— 总览只展示技能 + 武器
#   数据库全类型冲突角色 55 名 —— 含 review / ritual，本阶段不展示
# 用 55 去断言展示逻辑会误伤 YUI 这类只有评价冲突的角色，产生无上下文警告。
CONFLICT_SCOPE_CHARACTER: tuple[str, ...] = ("skill", "weapon")
CONFLICT_SCOPE_SKILL: tuple[str, ...] = ("skill",)
CONFLICT_SCOPE_WEAPON: tuple[str, ...] = ("weapon",)
GUIDE_TYPES: tuple[str, ...] = ("review", "setting", "operation", "party", "ritual", "innate")

_CHARACTER_COLUMNS = """
    source_key, name_zh, codename_zh, slug, element_zh, position_zh,
    rarity, description_zh, source_status, translation_status
"""


def _placeholders(count: int) -> str:
    """生成 ?,?,? 占位符。值仍然走参数绑定，这里只控制占位符个数。"""
    return ",".join("?" for _ in range(count))


class ArchiveRepository:
    def __init__(self, db: ArchiveDatabase):
        self._db = db

    # ---- 角色 ----

    def list_characters(self) -> tuple[Character, ...]:
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters ORDER BY rarity DESC, id"
        )
        return tuple(Character.from_row(r) for r in rows)

    def characters_by_name(self, term: str, limit: int) -> tuple[Character, ...]:
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters WHERE name_zh = ? LIMIT ?",
            (term, limit),
        )
        return tuple(Character.from_row(r) for r in rows)

    def characters_by_source_key(self, term: str, limit: int) -> tuple[Character, ...]:
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters WHERE source_key = ? LIMIT ?",
            (term, limit),
        )
        return tuple(Character.from_row(r) for r in rows)

    def characters_by_slug(self, term: str, limit: int) -> tuple[Character, ...]:
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters "
            f"WHERE slug IS NOT NULL AND LOWER(slug) = LOWER(?) LIMIT ?",
            (term, limit),
        )
        return tuple(Character.from_row(r) for r in rows)

    def characters_by_codename(self, term: str, limit: int) -> tuple[Character, ...]:
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters "
            f"WHERE codename_zh IS NOT NULL AND LOWER(codename_zh) = LOWER(?) LIMIT ?",
            (term, limit),
        )
        return tuple(Character.from_row(r) for r in rows)

    def characters_by_name_like(self, term: str, limit: int) -> tuple[Character, ...]:
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters "
            f"WHERE name_zh LIKE ? ORDER BY rarity DESC, id LIMIT ?",
            (f"%{term}%", limit),
        )
        return tuple(Character.from_row(r) for r in rows)

    def characters_by_any_like(self, term: str, limit: int) -> tuple[Character, ...]:
        pattern = f"%{term}%"
        rows = self._db.fetch_all(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters "
            f"WHERE name_zh LIKE ? OR source_key LIKE ? OR codename_zh LIKE ? OR slug LIKE ? "
            f"ORDER BY rarity DESC, id LIMIT ?",
            (pattern, pattern, pattern, pattern, limit),
        )
        return tuple(Character.from_row(r) for r in rows)

    def character_by_source_key(self, source_key: str) -> Character | None:
        row = self._db.fetch_one(
            f"SELECT {_CHARACTER_COLUMNS} FROM zh_characters WHERE source_key = ? LIMIT 1",
            (source_key,),
        )
        return Character.from_row(row) if row else None

    # ---- 技能 / 武器 ----

    def skills(self, character_key: str) -> tuple[Skill, ...]:
        rows = self._db.fetch_all(
            """
            SELECT character_key, skill_key, name_zh, element_zh, type_zh,
                   description_zh, sp, cooldown, translation_status
            FROM zh_skills WHERE character_key = ? ORDER BY id
            """,
            (character_key,),
        )
        return tuple(Skill.from_row(r) for r in rows)

    def weapons(self, character_key: str) -> tuple[Weapon, ...]:
        rows = self._db.fetch_all(
            """
            SELECT character_key, weapon_key, name_zh, rarity, health, attack,
                   defense, skill_name_zh, description_zh, translation_status
            FROM zh_weapons WHERE character_key = ? ORDER BY rarity DESC, id
            """,
            (character_key,),
        )
        return tuple(Weapon.from_row(r) for r in rows)

    def guides(
        self, character_key: str, guide_types: Sequence[str] = GUIDE_TYPES
    ) -> tuple[Guide, ...]:
        types = tuple(guide_types)
        if not types:
            return ()
        rows = self._db.fetch_all(
            "SELECT character_key, guide_type, content_json, source_status, translation_status "
            f"FROM zh_guides WHERE character_key = ? AND guide_type IN ({_placeholders(len(types))}) "
            "ORDER BY id",
            (character_key, *types),
        )
        return tuple(Guide.from_row(r) for r in rows)

    # ---- 数值冲突 ----

    def conflict_count(self, character_key: str, resource_types: Sequence[str]) -> int:
        """冲突必须从数据库算，不允许按角色名硬编码（§12）。"""
        types = tuple(resource_types)
        if not types:
            return 0
        sql = (
            "SELECT COUNT(*) FROM numeric_conflicts "
            f"WHERE character_key = ? AND resource_type IN ({_placeholders(len(types))}) "
            "AND numeric_conflict = 1"
        )
        return int(self._db.scalar(sql, (character_key, *types), default=0))

    # ---- 组合 ----

    def build_profile(
        self,
        character: Character,
        scope: Sequence[str] = CONFLICT_SCOPE_CHARACTER,
    ) -> CharacterProfile:
        return CharacterProfile(
            character=character,
            skills=self.skills(character.source_key),
            weapons=self.weapons(character.source_key),
            conflict_count=self.conflict_count(character.source_key, scope),
            conflict_scope="+".join(scope),
        )

    def build_guide_profile(
        self,
        character: Character,
        guide_types: Sequence[str],
    ) -> GuideProfile:
        types = tuple(guide_types)
        return GuideProfile(
            character=character,
            guides=self.guides(character.source_key, types),
            conflict_count=self.conflict_count(character.source_key, types),
            conflict_scope="+".join(types),
        )
