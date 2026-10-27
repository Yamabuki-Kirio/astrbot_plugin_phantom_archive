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
from .models import Character, CharacterProfile, Guide, GuideProfile, SearchResult, Skill, Weapon

SEPARATOR = "—" * 12

# 各类正文的软上限。到上限会退到最近的句末标点，而不是硬切。
CHARACTER_DESC_LIMIT = 60
SKILL_DESC_LIMIT = 160
WEAPON_DESC_LIMIT = 140

# 给「另有 N 条未显示」预留的空间
TRUNCATION_SLACK = 24

_SENTENCE_END = "。；！？!?;"
_WHITESPACE = re.compile(r"\s+")
_HTML_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_HANGUL_RUN = re.compile(r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]+")


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


def _plain(text) -> str:
    if text is None:
        return ""
    value = _HTML_BREAK.sub("\n", str(text))
    value = _HTML_TAG.sub("", value).replace("**", "").replace("> ", "")
    value = _HANGUL_RUN.sub("〔待译术语〕", value)
    return _WHITESPACE.sub(" ", value).strip()


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


# ------------------------------------------------------------------ 攻略 / 意识 / 配队

GUIDE_TITLES = {
    "review": "评价",
    "setting": "养成",
    "operation": "操作",
    "party": "配队",
    "ritual": "意识",
    "innate": "属性意识",
}


def _guide_tail(profile: GuideProfile) -> list[str]:
    statuses = [profile.character.translation_status]
    statuses.extend(guide.translation_status for guide in profile.guides)
    return _tail_lines(profile, statuses)


def _guide(profile: GuideProfile, guide_type: str) -> Guide | None:
    return next((item for item in profile.guides if item.guide_type == guide_type), None)


def _add_blocks(builder: MessageBuilder, blocks: list[list[str]]) -> int:
    shown = 0
    for block in blocks:
        if not builder.try_add(block):
            break
        shown += 1
    if shown < len(blocks):
        builder.try_add([f"另有 {len(blocks) - shown} 项未显示"])
    return shown


def _render_review(data: dict) -> list[list[str]]:
    blocks: list[list[str]] = []
    review = _clip_sentence(_plain(data.get("review")), 420)
    if review:
        blocks.append([review])
    for label, key in (("优点", "pros"), ("注意", "cons")):
        values = [_plain(item) for item in data.get(key, []) if _plain(item)]
        if values:
            blocks.append([f"■ {label}", *[f"· {item}" for item in values]])
    return blocks


def _render_setting(data: dict) -> list[list[str]]:
    blocks: list[list[str]] = []
    role = _plain(data.get("role"))
    if role:
        blocks.append([f"定位：{role}"])
    pairs = [
        ("主启示", data.get("main_revelation")),
        ("副启示", data.get("sub_revelation")),
        ("技能优先级", data.get("skill_priority")),
        ("心象属性", [data.get("mind_stats1"), data.get("mind_stats2")]),
        ("心象技能", [data.get("mind_skill1"), data.get("mind_skill2")]),
    ]
    for label, value in pairs:
        if isinstance(value, list):
            text = " / ".join(_plain(item) for item in value if _plain(item))
        else:
            text = _plain(value)
        if text:
            blocks.append([f"{label}：{text}"])
    minimum = data.get("minimum_stats") or {}
    stats = [f"{key} {value}" for key, value in minimum.items() if value not in (None, False, "")]
    if stats:
        blocks.append(["最低属性：" + "；".join(stats)])
    battle = _plain(data.get("battle_plus_stats"))
    if battle and battle.lower() != "nan":
        blocks.append([f"战斗加成：{battle}"])
    return blocks


def _render_operation(data: dict) -> list[list[str]]:
    blocks: list[list[str]] = []
    notes = [_plain(item).lstrip("· ") for item in data.get("note", []) if _plain(item)]
    if notes:
        blocks.append(["■ 操作说明", *[f"· {item}" for item in notes[:4]]])
    for plan in data.get("basic", []):
        if not isinstance(plan, dict):
            continue
        label = _plain(plan.get("label")) or "推荐循环"
        skills = [str(item).strip() for item in plan.get("skills", []) if str(item).strip()]
        value = _plain(plan.get("value"))
        sequence = " → ".join(skills) if skills else value
        if sequence:
            blocks.append([f"· {label}：{sequence}"])
    return blocks


def _render_party(data: dict) -> list[list[str]]:
    blocks: list[list[str]] = []
    for key, label in (("weapon", "推荐武器"), ("persona", "推荐人格面具"), ("party", "推荐队伍")):
        for item in data.get(key, []):
            if isinstance(item, str):
                text = _plain(item)
            elif isinstance(item, dict):
                names = item.get("members") or item.get("list") or item.get("name") or []
                if isinstance(names, str):
                    names = [names]
                text = " + ".join(_plain(name) for name in names if _plain(name))
                comment = _plain(item.get("comment"))
                if comment:
                    text += ("：" if text else "") + comment
            else:
                text = ""
            if text:
                blocks.append([f"· {label}：{text}"])
    for item in data.get("friends", []):
        if isinstance(item, dict):
            name, comment = _plain(item.get("name")), _plain(item.get("comment"))
            if name or comment:
                blocks.append([f"· {name or '推荐队友'}" + (f"：{comment}" if comment else "")])
    return blocks


def _render_ritual(data: dict) -> list[list[str]]:
    blocks: list[list[str]] = []
    for item in data.get("entries", []):
        if not isinstance(item, dict):
            continue
        key = _plain(item.get("key")).upper()
        name = _plain(item.get("name")) or "未收录名称"
        detail = _clip_sentence(_plain(item.get("detail")), 230)
        block = [f"· {key} {name}".strip()]
        if detail:
            block.append(f"  {detail}")
        blocks.append(block)
    return blocks


def _render_innate(data: dict) -> list[list[str]]:
    blocks: list[list[str]] = []
    for group in data.get("final_innate", []):
        for item in group if isinstance(group, list) else []:
            if not isinstance(item, dict):
                continue
            desc = _plain(item.get("desc"))
            value = _plain(item.get("value"))
            if desc:
                blocks.append([f"· {desc}" + (f"（{value}）" if value else "")])
    for item in data.get("innate_awake_skill", []):
        if not isinstance(item, dict):
            continue
        name = _plain(item.get("name")) or "未收录名称"
        desc = _clip_sentence(_plain(item.get("desc")), 240)
        block = [f"· {name}"]
        if desc:
            block.append(f"  {desc}")
        blocks.append(block)
    return blocks


_GUIDE_RENDERERS = {
    "review": _render_review,
    "setting": _render_setting,
    "operation": _render_operation,
    "party": _render_party,
    "ritual": _render_ritual,
    "innate": _render_innate,
}


def render_guide(profile: GuideProfile, guide_type: str, max_chars: int) -> str:
    title = GUIDE_TITLES.get(guide_type, "攻略")
    builder = MessageBuilder(max_chars, _guide_tail(profile))
    builder.add([f"【{profile.character.display_name}】{title}"])
    guide = _guide(profile, guide_type)
    if guide is None:
        builder.add(["", f"该角色暂无可显示的{title}资料。"])
        return builder.build()
    blocks = _GUIDE_RENDERERS[guide_type](guide.content)
    if not blocks:
        builder.add(["", f"该角色的{title}资料暂未收录有效内容。"])
    else:
        _add_blocks(builder, blocks)
    return builder.build()


def render_all_guides(profile: GuideProfile, max_chars: int) -> str:
    builder = MessageBuilder(max_chars, _guide_tail(profile))
    builder.add([f"【{profile.character.display_name}】综合攻略"])
    for guide in profile.guides:
        blocks = _GUIDE_RENDERERS.get(guide.guide_type, lambda _: [])(guide.content)
        if not blocks:
            continue
        section = [["", f"■ {GUIDE_TITLES.get(guide.guide_type, guide.guide_type)}"]] + blocks
        for block in section:
            if not builder.try_add(block):
                builder.try_add(["另有攻略内容未显示，请使用分类指令查看。"])
                return builder.build()
    if len(builder.lines) == 1:
        builder.add(["", "该角色暂无可显示的攻略资料。"])
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
            "/p5x意识 <名称>",
            "/p5x属性意识 <名称>",
            "/p5x养成 <名称>",
            "/p5x操作 <名称>",
            "/p5x评价 <名称>",
            "/p5x配队 <名称>",
            "/p5x攻略 <名称>",
            "",
            "支持中文名、原名、英文代号和 slug 查询。",
            "支持角色、技能、武器、意识、养成、操作、评价、配队和综合攻略查询。",
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
