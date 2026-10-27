"""消息渲染：按「完整块预算」排版，绝不切半句话（§9）。

优先级：
    角色标题 → 基础信息 → 核心技能/武器 → 未显示数量
    → 来源状态 → 数值冲突警告 → 免责声明

尾部三块（来源 / 警告 / 免责声明）先算长度并从预算里扣除，
正文只做整块取舍；放不下就整条省略并注明「另有 N 条未显示」。
"""

from __future__ import annotations

import re

from . import notices
from .models import Character, CharacterProfile, SearchResult, Skill, Weapon

SEPARATOR = "—" * 12

# 各类正文的软上限。到上限会退到最近的句末标点，而不是硬切。
CHARACTER_DESC_LIMIT = 60
SKILL_DESC_LIMIT = 160
WEAPON_DESC_LIMIT = 140

# 给「另有 N 条未显示」预留的空间
TRUNCATION_SLACK = 24

_SENTENCE_END = "。；！？!?;"
_WHITESPACE = re.compile(r"\s+")


def _clip_sentence(text: str | None, limit: int) -> str:
    """按句末标点截断，找不到句末就整段保留。

    宁可让整块被预算挤掉，也不输出半句话。
    """
    if not text:
        return ""
    flat = _WHITESPACE.sub(" ", str(text)).strip()
    if len(flat) <= limit:
        return flat

    window = flat[:limit]
    cut = max(window.rfind(ch) for ch in _SENTENCE_END)
    if cut <= 0:
        # 没有句末标点：不切，交给预算决定是否整块显示
        return flat
    return window[: cut + 1] + "…"


def _fmt_num(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _join_meta(parts) -> str:
    return " · ".join(str(p) for p in parts if p is not None and str(p).strip())


def _skill_label(skill: Skill) -> str:
    name = (skill.name_zh or "").strip()
    return name or notices.MISSING_SKILL_NAME_TEMPLATE.format(key=skill.skill_key)


def _weapon_label(weapon: Weapon) -> str:
    name = (weapon.name_zh or "").strip()
    return name or notices.MISSING_WEAPON_NAME_TEMPLATE.format(key=weapon.weapon_key)


def _skill_meta(skill: Skill, *, with_cooldown: bool) -> str:
    parts: list[str] = []
    if skill.category:
        parts.append(skill.category)
    if skill.sp:
        parts.append(f"SP {skill.sp}")
    # cooldown = 0 在源数据里表示「无冷却」，显示成 0 反而误导
    if with_cooldown and skill.cooldown:
        parts.append(f"冷却 {skill.cooldown} 回合")
    return _join_meta(parts)


class MessageBuilder:
    """带预算的消息拼装器。"""

    def __init__(self, max_chars: int, tail_lines: list[str], slack: int = TRUNCATION_SLACK):
        self.max_chars = max(200, int(max_chars))
        self.lines: list[str] = []
        self.tail_text = "\n".join(tail_lines)
        self.slack = slack

    @property
    def _reserved(self) -> int:
        # +1 是正文与尾部之间的换行
        return len(self.tail_text) + 1 + self.slack

    def try_add(self, block: list[str]) -> bool:
        """整块塞得下就塞，塞不下返回 False 且不改变现状。"""
        candidate = self.lines + list(block)
        if len("\n".join(candidate)) + self._reserved > self.max_chars:
            return False
        self.lines = candidate
        return True

    def add(self, block: list[str]) -> None:
        """标题、基础信息这类必留内容，不做预算判断。"""
        self.lines.extend(block)

    def build(self) -> str:
        return "\n".join(self.lines) + "\n" + self.tail_text


def _tail_lines(profile: CharacterProfile, statuses) -> list[str]:
    """来源状态 → 数值冲突警告 → 分隔线 → 免责声明。"""
    worst = notices.worst_translation_status(statuses)
    lines = [
        "",
        f"来源：{notices.source_label(profile.character.source_status)}"
        f"｜{notices.translation_label(worst)}",
    ]
    if profile.has_conflict:
        lines.append(notices.NUMERIC_CONFLICT_WARNING)
    lines.append(SEPARATOR)
    lines.append(notices.DISCLAIMER)
    return lines


def _character_header(character: Character) -> list[str]:
    badge = ""
    if character.translation_status == "needs_review":
        badge = f" {notices.NEEDS_REVIEW_BADGE}"
    lines = [f"【{character.display_name}】{badge}"]
    meta = _join_meta(
        [
            f"{character.rarity}★" if character.rarity else None,
            character.element_zh,
            character.position_zh,
        ]
    )
    if meta:
        lines.append(meta)
    return lines


# ------------------------------------------------------------------ 角色


def render_character(profile: CharacterProfile, max_chars: int) -> str:
    character = profile.character
    statuses = [character.translation_status]
    statuses += [s.translation_status for s in profile.skills]
    statuses += [w.translation_status for w in profile.weapons]

    builder = MessageBuilder(max_chars, _tail_lines(profile, statuses))
    builder.add(_character_header(character))

    intro = _clip_sentence(character.description_zh, CHARACTER_DESC_LIMIT)
    if intro:
        builder.add([intro])

    if profile.skills:
        builder.try_add(["", "■ 技能"])
        shown = 0
        for skill in profile.skills:
            meta = _skill_meta(skill, with_cooldown=False)
            line = f"· {_skill_label(skill)}" + (f"（{meta}）" if meta else "")
            if not builder.try_add([line]):
                break
            shown += 1
        if shown < len(profile.skills):
            builder.try_add([f"  另有 {len(profile.skills) - shown} 条未显示"])

    if profile.weapons:
        builder.try_add(["", "■ 武器"])
        shown = 0
        for weapon in profile.weapons:
            meta = " ".join(
                part
                for part in [
                    f"{weapon.rarity}★" if weapon.rarity else "",
                    f"攻击 {_fmt_num(weapon.attack)}" if weapon.attack else "",
                ]
                if part
            )
            line = f"· {_weapon_label(weapon)}" + (f" {meta}" if meta else "")
            if not builder.try_add([line]):
                break
            shown += 1
        if shown < len(profile.weapons):
            builder.try_add([f"  另有 {len(profile.weapons) - shown} 条未显示"])

    return builder.build()


# ------------------------------------------------------------------ 技能


def render_skills(profile: CharacterProfile, max_chars: int) -> str:
    character = profile.character
    statuses = [character.translation_status] + [s.translation_status for s in profile.skills]

    builder = MessageBuilder(max_chars, _tail_lines(profile, statuses))
    builder.add([f"【{character.display_name}】技能"])

    if not profile.skills:
        builder.add(["", "该角色暂无可显示的技能资料。"])
        return builder.build()

    builder.add([f"共 {len(profile.skills)} 条"])

    shown = 0
    for skill in profile.skills:
        meta = _skill_meta(skill, with_cooldown=True)
        block = [f"· {_skill_label(skill)}" + (f"（{meta}）" if meta else "")]
        body = _clip_sentence(skill.description_zh, SKILL_DESC_LIMIT)
        if body:
            block.append(f"  {body}")
        if not builder.try_add(block):
            break
        shown += 1

    if shown < len(profile.skills):
        builder.try_add([f"另有 {len(profile.skills) - shown} 条未显示"])

    return builder.build()


# ------------------------------------------------------------------ 武器


def render_weapons(profile: CharacterProfile, max_chars: int) -> str:
    character = profile.character
    statuses = [character.translation_status] + [w.translation_status for w in profile.weapons]

    builder = MessageBuilder(max_chars, _tail_lines(profile, statuses))
    builder.add([f"【{character.display_name}】武器"])

    if not profile.weapons:
        builder.add(["", "该角色暂无可显示的武器资料。"])
        return builder.build()

    builder.add([f"共 {len(profile.weapons)} 把"])

    shown = 0
    for weapon in profile.weapons:
        title = _weapon_label(weapon)
        if weapon.rarity:
            title += f" {weapon.rarity}★"
        block = [f"· {title}"]

        stats = _join_meta(
            [
                f"生命 {_fmt_num(weapon.health)}" if weapon.health else None,
                f"攻击 {_fmt_num(weapon.attack)}" if weapon.attack else None,
                f"防御 {_fmt_num(weapon.defense)}" if weapon.defense else None,
            ]
        )
        if stats:
            block.append(f"  {stats}")

        skill_name = (weapon.skill_name_zh or "").strip()
        block.append(
            f"  {notices.MISSING_WEAPON_SKILL_NAME}"
            if not skill_name
            else f"  技能名称：{skill_name}"
        )

        body = _clip_sentence(weapon.description_zh, WEAPON_DESC_LIMIT)
        if body:
            block.append(f"  {body}")

        if not builder.try_add(block):
            break
        shown += 1

    if shown < len(profile.weapons):
        builder.try_add([f"另有 {len(profile.weapons) - shown} 条未显示"])

    return builder.build()


# ------------------------------------------------------------------ 非数据回复


def render_help() -> str:
    return "\n".join(
        [
            "【夜幕档案】",
            "",
            "可用指令：",
            "/p5x角色 <名称>",
            "/p5x技能 <名称>",
            "/p5x武器 <名称>",
            "/p5x角色列表",
            "",
            "支持中文名、原名、英文代号和 slug 查询。",
            "当前仅开放角色、技能和武器资料。",
            "",
            notices.HELP_SOURCE_NOTE,
        ]
    )


def render_not_found(term: str) -> str:
    return (
        f"没有找到“{term}”对应的角色。\n"
        "可发送 /p5x角色列表 查看当前收录角色。"
    )


def render_multiple(result: SearchResult) -> str:
    lines = ["找到多个可能的角色："]
    for index, candidate in enumerate(result.candidates, start=1):
        lines.append(f"{index}. {candidate.display_name}")
    lines.append("")
    lines.append("请使用更完整的名称重新查询。")
    return "\n".join(lines)


def render_usage(command: str, example: str) -> str:
    return f"用法：{command} <角色名称>\n例如：{command} {example}"


def render_character_list(characters: tuple[Character, ...], max_chars: int) -> str:
    builder = MessageBuilder(
        max_chars,
        [SEPARATOR, notices.DISCLAIMER_ONELINE],
        slack=0,
    )
    builder.add([f"【夜幕档案】当前收录 {len(characters)} 名角色", ""])

    shown = 0
    for character in characters:
        meta = _join_meta(
            [
                f"{character.rarity}★" if character.rarity else None,
                character.element_zh,
                character.position_zh,
            ]
        )
        line = f"· {character.display_name}" + (f" {meta}" if meta else "")
        if not builder.try_add([line]):
            break
        shown += 1

    if shown < len(characters):
        builder.try_add([f"另有 {len(characters) - shown} 名未显示"])

    return builder.build()


# ------------------------------------------------------------------ 错误提示


def render_database_missing() -> str:
    return "夜幕档案数据库尚未安装，请联系机器人管理员。"


def render_schema_mismatch() -> str:
    return "夜幕档案数据库版本不兼容，请联系机器人管理员更新。"


def render_query_failed() -> str:
    return "查询失败，请稍后重试或联系机器人管理员。"
