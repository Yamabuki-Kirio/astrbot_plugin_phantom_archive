"""技能加点推荐：只从养成攻略的结构化字段里读，绝不猜。

数据来源（唯一）：
    ``zh_guides`` 中 ``guide_type='setting'`` 的 ``skill_priority`` 字段，
    形如 ``"3>1>4>2"``，数字是技能槽位编号。

槽位规则（依据数据本身，不是猜测）：
    1/2/3 → 对应 ``skill1`` / ``skill2`` / ``skill3`` 行的中文技能名；
    4     → 如果该角色真有 ``skill4`` 行（例如 J&C）就用它，
            否则第 4 槽是「战斗技巧」—— 全部 69 条养成数据的
            ``skill_levels`` 都恰好是 skill1~skill4 四个槽，说明第 4 槽
            客观存在，只是不在 zh_skills 里。
    其它编号 → 保留原编号并标「待核对」，不编造名字。

明确不做的事：
- 不按技能伤害/倍率反推优先级；
- 不拿 zh_skills 的排列顺序当推荐顺序；
- 没有数据就显示「暂未收录」，不留空、不生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from . import notices

BATTLE_SLOT_LABEL = "战斗技巧"
SKILL_SLOT_PREFIX = "skill"

SEPARATORS = re.compile(r"[>＞→,，、/／\s]+")
DIGITS = re.compile(r"^\d{1,2}$")

MAX_NOTES = 3
UNAVAILABLE_TEXT = "暂未收录可靠的技能加点顺序"


@dataclass(frozen=True)
class SkillUpgradeRecommendation:
    """一条经过解析的加点推荐。

    ``available=False`` 表示没有可靠数据 —— 此时展示层必须明确写出「暂未收录」，
    而不是隐藏整个板块，否则用户会以为漏了。
    """

    ordered_skill_keys: tuple[str, ...] = ()
    ordered_skill_names: tuple[str, ...] = ()
    summary: str = ""
    source_status: str = ""
    translation_status: str = ""
    needs_review: bool = False
    # ---- 展示用附加字段（都有默认值，方便构造空对象） ----
    available: bool = False
    priority_labels: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    raw_priority: str = ""

    @property
    def order_text(self) -> str:
        return " → ".join(self.ordered_skill_names)

    @property
    def priority_text(self) -> str:
        return " ＞ ".join(self.priority_labels)

    @property
    def review_suffix(self) -> str:
        return notices.NEEDS_REVIEW_BADGE if self.needs_review else ""


UNAVAILABLE = SkillUpgradeRecommendation()

# 模板里也要用同一句「暂未收录」文案
UNAVAILABLE_LABEL = UNAVAILABLE_TEXT


def _skill_map(skills: Iterable[Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for skill in skills or ():
        key = getattr(skill, "skill_key", "") or ""
        if key:
            mapping[key] = skill
    return mapping


def _slot_tokens(raw: str) -> list[str]:
    return [token for token in SEPARATORS.split(raw.strip()) if token]


def _label_for_slot(number: str, skills: Mapping[str, Any]) -> tuple[str, str, bool]:
    """返回 (展示标签, 技能 key, 是否需要人工核对)。"""
    skill = skills.get(f"{SKILL_SLOT_PREFIX}{number}")
    if skill is not None:
        name = (getattr(skill, "name_zh", None) or "").strip()
        if not name:
            name = notices.MISSING_SKILL_NAME_TEMPLATE.format(
                key=f"{SKILL_SLOT_PREFIX}{number}"
            )
            return f"技能{number}·{name}", f"{SKILL_SLOT_PREFIX}{number}", True
        return f"技能{number}·{name}", f"{SKILL_SLOT_PREFIX}{number}", False

    if number == "4":
        # 第 4 槽存在但不在 zh_skills 里，游戏内叫「战斗技巧」
        return BATTLE_SLOT_LABEL, "", False

    return f"技能{number}", "", True


def _level_summary(levels: Any, labels: Mapping[str, str]) -> str:
    if not isinstance(levels, dict):
        return ""
    parts = []
    for slot in ("1", "2", "3", "4"):
        value = levels.get(f"{SKILL_SLOT_PREFIX}{slot}")
        if not isinstance(value, str) or not value.strip():
            continue
        label = labels.get(slot, f"技能{slot}")
        parts.append(f"{label} {value.strip()}")
    if not parts:
        return ""
    return "加点目标：" + "｜".join(parts[:4])


def parse(
    content: Any,
    skills: Iterable[Any],
    *,
    source_status: str = "",
    translation_status: str = "",
) -> SkillUpgradeRecommendation:
    """从养成攻略 JSON 解析加点推荐。拿不到可靠数据就返回 UNAVAILABLE。"""
    if not isinstance(content, dict):
        return UNAVAILABLE

    raw = content.get("skill_priority")
    if not isinstance(raw, str) or not raw.strip():
        return UNAVAILABLE

    tokens = _slot_tokens(raw)
    if not tokens:
        return UNAVAILABLE

    skill_index = _skill_map(skills)
    labels: list[str] = []
    keys: list[str] = []
    names: list[str] = []
    slot_labels: dict[str, str] = {}
    needs_review = False
    unknown: list[str] = []
    unresolved_slots: list[str] = []

    for token in tokens:
        if not DIGITS.match(token):
            needs_review = True
            unknown.append(token)
            continue
        number = str(int(token))
        label, key, review = _label_for_slot(number, skill_index)
        labels.append(label)
        slot_labels[number] = label
        needs_review = needs_review or review
        if key:
            keys.append(key)
            name = (getattr(skill_index[key], "name_zh", None) or "").strip()
            names.append(name or label)
        elif number != "4":
            # 既不是已知技能槽、也不是第 4 槽「战斗技巧」
            unresolved_slots.append(number)

    if not labels:
        return UNAVAILABLE

    notes: list[str] = []
    summary = _level_summary(content.get("skill_levels"), slot_labels)
    if summary:
        notes.append(summary)
    if unknown:
        notes.append(f"编号 {'、'.join(unknown)} 未能识别，待核对")
    if unresolved_slots:
        notes.append(f"技能 {'、'.join(unresolved_slots)} 未能对应到技能表，待核对")
    if needs_review and not unknown and not unresolved_slots:
        notes.append("部分技能名暂未收录，待核对")

    return SkillUpgradeRecommendation(
        ordered_skill_keys=tuple(keys),
        ordered_skill_names=tuple(names),
        summary=summary,
        source_status=source_status or "",
        translation_status=translation_status or "",
        needs_review=needs_review,
        available=True,
        priority_labels=tuple(labels),
        notes=tuple(notes[:MAX_NOTES]),
        raw_priority=raw.strip(),
    )


__all__ = [
    "SkillUpgradeRecommendation",
    "UNAVAILABLE",
    "UNAVAILABLE_TEXT",
    "BATTLE_SLOT_LABEL",
    "parse",
]
