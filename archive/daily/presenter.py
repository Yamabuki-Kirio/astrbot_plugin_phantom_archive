"""每日运势的展示层：一份 :class:`DailyFortune` → 一张卡片 / 一段文字。

文字和图片**必须**由同一个对象生成 —— 图挂了就换文字，内容不能变。
所以这里只有两个纯函数，谁都不重新抽签、不重新抽角色。

排版取舍：塔罗、灵签、卦象三块固定都显示，不做分页。
每日运势是「一眼看完」的东西，翻页反而破坏体验；
真正需要克制的长文本（卦辞）已经在数据层裁到一两句。
"""

from __future__ import annotations

from .. import cards

CATEGORY = "每日运势"
TEMPLATE_NAME = "daily_fortune.html"

# 页脚固定说明。这里不引用 notices.DISCLAIMER_LINES：
# 那三行讲的是「社区资料 / 机器翻译」，和随机运势无关，
# 混用会让用户以为角色数据也是随机生成的。
DISCLAIMER_LINES: tuple[str, ...] = (
    "本卡片由随机算法生成，仅供娱乐参考，不构成任何建议。",
    "角色立绘版权归游戏发行方所有，仅限个人自用。",
    "同一用户当天结果固定，次日按配置时区刷新。",
)

SOURCE_LABEL = "P5X 国服官网（角色立绘）"
GENERATION_LABEL = "随机生成（仅供娱乐）"

# 文字降级时的固定尾注，与图片页脚保持同一句
ENTERTAINMENT_NOTE = "※ 同一用户当天结果固定，次日刷新，仅供娱乐参考。"

TITLE_MAX_CHARS = 24


def _short(text: str, limit: int = TITLE_MAX_CHARS) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def character_line(fortune) -> str:
    """「YUI（5★｜电击属性｜优越）」这样的单行描述。"""
    name = fortune.character.name
    subtitle = fortune.character.subtitle
    return f"{name}（{subtitle}）" if subtitle else name


# ---------------------------------------------------------------- 文字


def render_text(fortune) -> str:
    """T2I 不可用时的降级文本。与图片内容一一对应。"""
    lines = [f"【P5X 每日运势】{fortune.day_label}", ""]
    lines.append(f"今日角色：{character_line(fortune)}")

    tarot = fortune.tarot
    if tarot.name:
        lines.append(f"塔罗指引：{tarot.label}")
        if tarot.meaning:
            lines.append(f"  → {tarot.meaning}")

    qian = fortune.lingqian
    if qian.grade:
        lines.append(f"观音灵签：{qian.label}")
        poem = " ".join(qian.poem_lines)
        if poem:
            lines.append(f"  签诗：{poem}")
        if qian.explain:
            lines.append(f"  解曰：{qian.explain}")

    gua = fortune.zhouyi
    if gua.name:
        trigram = f"（{gua.trigram_label}）" if gua.trigram_label else ""
        lines.append(f"周易卦象：{gua.label}{trigram}")
        if gua.guaci:
            lines.append(f"  卦辞：{gua.guaci}")
        if gua.jie:
            lines.append(f"  简解：{gua.jie}")
        if gua.moving and gua.changed_name:
            lines.append(f"  动爻：{gua.moving_label}，变{gua.changed_name}卦")

    if fortune.advice:
        lines.append("")
        lines.append(f"综合建议：{fortune.advice}")

    lines.append("")
    lines.append(ENTERTAINMENT_NOTE)
    return "\n".join(lines)


# ---------------------------------------------------------------- 卡片


def _tarot_block(fortune) -> cards.Block:
    tarot = fortune.tarot
    body = [f"  {tarot.meaning}"] if tarot.meaning else ["  牌面释义暂缺"]
    return cards.Block(kind="item", lines=(f"· {tarot.label}", *body))


def _lingqian_block(fortune) -> cards.Block:
    qian = fortune.lingqian
    body = [f"  {line}" for line in qian.poem_lines]
    if qian.explain:
        body.append(f"  解曰：{qian.explain}")
    return cards.Block(kind="item", lines=(f"· {qian.label}", *body))


def _zhouyi_block(fortune) -> cards.Block:
    gua = fortune.zhouyi
    trigram = f"（{gua.trigram_label}）" if gua.trigram_label else ""
    lines = [f"本卦：{gua.label}{trigram}"]
    if gua.guaci:
        lines.append(f"卦辞：{gua.guaci}")
    if gua.jie:
        lines.append(f"简解：{gua.jie}")
    if gua.moving and gua.changed_name:
        lines.append(f"动爻：{gua.moving_label}，变{gua.changed_name}卦")
    else:
        lines.append("动爻：六爻安静，宜守其常")
    return cards.Block(kind="kv", lines=tuple(lines))


def build_sections(fortune) -> tuple[cards.Section, ...]:
    sections = [
        cards.Section(heading="塔罗指引", blocks=(_tarot_block(fortune),)),
        cards.Section(heading="观音灵签", blocks=(_lingqian_block(fortune),)),
        cards.Section(heading="周易卦象", blocks=(_zhouyi_block(fortune),)),
    ]
    if fortune.advice:
        sections.append(
            cards.Section(
                heading="综合建议",
                blocks=(cards.Block(kind="para", lines=(fortune.advice,)),),
            )
        )
    return tuple(sections)


def build_page(fortune) -> cards.CardPage:
    """一张卡片。角色名是最大字号，所以放在 title；属性定位放 subtitle。"""
    character = fortune.character
    hero_key = (character.slug or "").strip() or character.key
    return cards.CardPage(
        category=CATEGORY,
        title=_short(character.name),
        subtitle=character.subtitle,
        lead=f"{fortune.day_label} · 今日角色",
        sections=build_sections(fortune),
        page_index=1,
        page_total=1,
        source_label=SOURCE_LABEL,
        translation_label=GENERATION_LABEL,
        has_conflict=False,
        show_footer=True,
        hero_kind="character",
        hero_key=hero_key,
        # 立绘有多张时按日期挑一张：同一天固定，换一天换一张
        hero_variant=fortune.date_key,
        disclaimer_lines=DISCLAIMER_LINES,
    )


__all__ = [
    "CATEGORY",
    "TEMPLATE_NAME",
    "DISCLAIMER_LINES",
    "ENTERTAINMENT_NOTE",
    "GENERATION_LABEL",
    "SOURCE_LABEL",
    "build_page",
    "build_sections",
    "character_line",
    "render_text",
]
