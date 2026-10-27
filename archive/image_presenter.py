"""把查询结果变成卡片展示模型。

这一层只做「数据 → 排版块」，不生成 HTML、不发请求、不碰数据库、不查素材。
素材匹配交给 asset_resolver，图片编码交给 image_codec ——
这里只写业务标识（hero_kind / hero_key / ref_key），视觉层自己去解析。
"""

from __future__ import annotations

from . import cards, notices, renderer
from .models import Character, CharacterProfile, GuideProfile

# 单张卡片里正文的软上限，避免一段话把整页吃掉
CHARACTER_DESC_LIMIT = 80
SKILL_DESC_LIMIT = 170
WEAPON_DESC_LIMIT = 150
RITUAL_DESC_LIMIT = 230
REVIEW_DESC_LIMIT = 420

CATEGORY_CHARACTER = "角色"
CATEGORY_SKILL = "技能"
CATEGORY_WEAPON = "武器"
CATEGORY_LIST = "角色列表"

HERO_CHARACTER = "character"
HERO_WEAPON = "weapon"
HERO_ARCHIVE = "archive"


def _subtitle(character: Character) -> str:
    return renderer.join_meta(
        [
            f"{character.rarity}★" if character.rarity else None,
            character.element_zh,
            character.position_zh,
        ]
    )


def _character_key(character: Character) -> str:
    """素材按 slug 匹配，没有 slug 才退回 source_key。"""
    return (character.slug or "").strip() or character.source_key


def _character_statuses(profile: CharacterProfile) -> tuple:
    return (profile.character.translation_status,) + tuple(
        skill.translation_status for skill in profile.skills
    ) + tuple(weapon.translation_status for weapon in profile.weapons)


def _finish(pages, statuses, has_conflict: bool, source_status: str | None):
    """统一补齐页脚信息。来源状态按记录实际值取，缺省回落到 community。"""
    source = notices.source_label(source_status or "community")
    translation = notices.translation_label(notices.worst_translation_status(statuses))
    return [
        cards.with_footer(
            page,
            source_label=source,
            translation_label=translation,
            has_conflict=has_conflict,
        )
        for page in pages
    ]


# ------------------------------------------------------------------ 技能加点推荐


def recommendation_section(profile: CharacterProfile) -> cards.Section | None:
    """技能加点推荐板块。没有可靠数据也要出板块，写「暂未收录」。"""
    recommendation = getattr(profile, "recommendation", None)
    if recommendation is None:
        return None

    lines = renderer.recommendation_lines(recommendation)
    if not lines:
        return None

    body = lines[1:]  # 第一行是「■ 技能加点推荐」，这里作为节标题
    blocks = tuple(cards.Block(kind="para", lines=(line,)) for line in body)
    return cards.Section(heading="技能加点推荐", blocks=blocks)


# ------------------------------------------------------------------ 角色 / 技能 / 武器


def character_pages(profile: CharacterProfile, *, rows_per_page: int, max_pages: int):
    character = profile.character
    blocks: list[list[str]] = []

    for skill in profile.skills:
        meta = renderer.skill_meta(skill, with_cooldown=False)
        line = f"· {renderer.skill_label(skill)}" + (f"（{meta}）" if meta else "")
        blocks.append([line])

    weapon_blocks: list[list[str]] = []
    for weapon in profile.weapons:
        meta = " ".join(
            part
            for part in [
                f"{weapon.rarity}★" if weapon.rarity else "",
                f"攻击 {renderer.fmt_num(weapon.attack)}" if weapon.attack else "",
            ]
            if part
        )
        weapon_blocks.append([f"· {renderer.weapon_label(weapon)}" + (f" {meta}" if meta else "")])

    # 顺序即优先级：技能加点推荐必须在第一页，所以它排在最前
    sections: list[cards.Section] = []
    advice = recommendation_section(profile)
    if advice is not None:
        sections.append(advice)
    if blocks:
        sections.append(
            cards.Section(heading="技能", blocks=tuple(cards.classify_block(b) for b in blocks))
        )
    if weapon_blocks:
        sections.append(
            cards.Section(heading="武器", blocks=tuple(cards.classify_block(b) for b in weapon_blocks))
        )

    key = _character_key(character)
    pages = cards.paginate(
        CATEGORY_CHARACTER,
        character.display_name,
        tuple(sections),
        subtitle=_subtitle(character),
        lead=renderer.clip_sentence(character.description_zh, CHARACTER_DESC_LIMIT),
        rows_per_page=rows_per_page,
        max_pages=max_pages,
        hero_kind=HERO_CHARACTER,
        hero_key=key,
        owner_key=key,
    )
    return _finish(pages, _character_statuses(profile), profile.has_conflict, character.source_status)


def skills_pages(profile: CharacterProfile, *, rows_per_page: int, max_pages: int):
    character = profile.character
    blocks: list[list[str]] = []
    for skill in profile.skills:
        meta = renderer.skill_meta(skill, with_cooldown=True)
        block = [f"· {renderer.skill_label(skill)}" + (f"（{meta}）" if meta else "")]
        body = renderer.clip_sentence(skill.description_zh, SKILL_DESC_LIMIT)
        if body:
            block.append(f"  {body}")
        blocks.append(block)

    sections = (
        (
            cards.Section(
                heading=f"技能（共 {len(profile.skills)} 条）",
                blocks=tuple(cards.classify_block(b) for b in blocks),
            ),
        )
        if blocks
        else ()
    )
    key = _character_key(character)
    pages = cards.paginate(
        CATEGORY_SKILL,
        character.display_name,
        sections,
        subtitle=_subtitle(character),
        rows_per_page=rows_per_page,
        max_pages=max_pages,
        hero_kind=HERO_CHARACTER,
        hero_key=key,
        owner_key=key,
    )
    return _finish(pages, _character_statuses(profile), profile.has_conflict, character.source_status)


def weapons_pages(profile: CharacterProfile, *, rows_per_page: int, max_pages: int):
    character = profile.character
    blocks: list[cards.Block] = []
    for weapon in profile.weapons:
        title = renderer.weapon_label(weapon)
        if weapon.rarity:
            title += f" {weapon.rarity}★"
        lines = [f"· {title}"]
        stats = renderer.join_meta(
            [
                f"生命 {renderer.fmt_num(weapon.health)}" if weapon.health else None,
                f"攻击 {renderer.fmt_num(weapon.attack)}" if weapon.attack else None,
                f"防御 {renderer.fmt_num(weapon.defense)}" if weapon.defense else None,
            ]
        )
        if stats:
            lines.append(f"  {stats}")
        skill_name = (weapon.skill_name_zh or "").strip()
        lines.append(
            f"  {notices.MISSING_WEAPON_SKILL_NAME}"
            if not skill_name
            else f"  技能名称：{skill_name}"
        )
        body = renderer.clip_sentence(weapon.description_zh, WEAPON_DESC_LIMIT)
        if body:
            lines.append(f"  {body}")

        classified = cards.classify_block(lines)
        # ref_key 让视觉层知道这块是哪把武器，从而挂缩略图
        blocks.append(
            cards.Block(
                kind=classified.kind, lines=classified.lines, ref_key=weapon.weapon_key
            )
        )

    sections = (
        (cards.Section(heading=f"武器（共 {len(profile.weapons)} 把）", blocks=tuple(blocks)),)
        if blocks
        else ()
    )
    owner = _character_key(character)
    pages = cards.paginate(
        CATEGORY_WEAPON,
        character.display_name,
        sections,
        subtitle=_subtitle(character),
        rows_per_page=rows_per_page,
        max_pages=max_pages,
        hero_kind=HERO_WEAPON,
        owner_key=owner,
    )

    # 「当前页第一把武器」只有分页之后才知道，所以在这里回填
    result = []
    for page in pages:
        first_key = next(
            (
                block.ref_key
                for section in page.sections
                for block in section.blocks
                if block.ref_key
            ),
            "",
        )
        result.append(
            cards.with_hero(
                page,
                kind=HERO_WEAPON if first_key else HERO_CHARACTER,
                key=first_key or owner,
                owner=owner,
            )
        )
    return _finish(
        result, _character_statuses(profile), profile.has_conflict, character.source_status
    )


# ------------------------------------------------------------------ 攻略类


def _guide_statuses(profile: GuideProfile) -> tuple:
    return (profile.character.translation_status,) + tuple(
        guide.translation_status for guide in profile.guides
    )


def guide_pages(
    profile: GuideProfile,
    guide_type: str,
    *,
    rows_per_page: int,
    max_pages: int,
):
    title = renderer.GUIDE_TITLES.get(guide_type, "攻略")
    guide = next((item for item in profile.guides if item.guide_type == guide_type), None)
    blocks: list[list[str]] = []
    if guide is not None:
        builder = renderer.GUIDE_BLOCK_BUILDERS.get(guide_type)
        if builder is not None:
            blocks = builder(guide.content)

    sections = cards.split_sections(blocks) if blocks else ()
    key = _character_key(profile.character)
    pages = cards.paginate(
        f"{title}攻略",
        profile.character.display_name,
        sections,
        subtitle=_subtitle(profile.character),
        rows_per_page=rows_per_page,
        max_pages=max_pages,
        hero_kind=HERO_CHARACTER,
        hero_key=key,
        owner_key=key,
    )
    return _finish(
        pages, _guide_statuses(profile), profile.has_conflict, profile.character.source_status
    )


def all_guides_pages(profile: GuideProfile, *, rows_per_page: int, max_pages: int):
    sections: list[cards.Section] = []
    for guide in profile.guides:
        builder = renderer.GUIDE_BLOCK_BUILDERS.get(guide.guide_type)
        if builder is None:
            continue
        blocks = builder(guide.content)
        if not blocks:
            continue
        heading = renderer.GUIDE_TITLES.get(guide.guide_type, guide.guide_type)
        for section in cards.split_sections(blocks):
            # 综合攻略里每类都要带上自己的名字，否则读者不知道在看哪一节
            prefix = heading if not section.heading else f"{heading} · {section.heading}"
            sections.append(cards.Section(heading=prefix, blocks=section.blocks))

    key = _character_key(profile.character)
    pages = cards.paginate(
        "综合攻略",
        profile.character.display_name,
        tuple(sections),
        subtitle=_subtitle(profile.character),
        rows_per_page=rows_per_page,
        max_pages=max_pages,
        hero_kind=HERO_CHARACTER,
        hero_key=key,
        owner_key=key,
    )
    return _finish(
        pages, _guide_statuses(profile), profile.has_conflict, profile.character.source_status
    )


# ------------------------------------------------------------------ 角色列表


def character_list_pages(
    characters: tuple[Character, ...],
    *,
    rows_per_page: int,
    max_pages: int,
):
    blocks: list[cards.Block] = []
    for character in characters:
        meta = renderer.join_meta(
            [
                f"{character.rarity}★" if character.rarity else None,
                character.element_zh,
                character.position_zh,
            ]
        )
        line = f"· {character.display_name}" + (f" {meta}" if meta else "")
        blocks.append(cards.Block(kind="item", lines=(line,)))

    sections = (
        cards.Section(heading=f"收录角色（共 {len(characters)} 名）", blocks=tuple(blocks)),
    )
    pages = cards.paginate(
        CATEGORY_LIST,
        "夜幕档案",
        sections,
        subtitle="当前收录角色",
        rows_per_page=rows_per_page,
        max_pages=max_pages,
        hero_kind=HERO_ARCHIVE,
    )
    return _finish(pages, tuple(c.translation_status for c in characters), False, None)
