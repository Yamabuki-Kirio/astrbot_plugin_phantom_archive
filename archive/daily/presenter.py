"""每日运势的展示层：一份 :class:`DailyFortune` → 一张卡片 / 一段文字。

文字和图片**必须**由同一个对象生成 —— 图挂了就换文字，内容不能变。
所以这里只有两个纯函数，谁都不重新抽签、不重新抽角色。

排版取舍：只保留「今日角色 + 塔罗」，不做分页。
每日运势是「一眼看完」的东西，翻页反而破坏体验；
正文分两块：塔罗（五档评级 + 宜忌）与综合建议（结合牌义展开的一句话）。
"""

from __future__ import annotations

from .. import cards
from . import fortune_grade

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


def tarot_label(tarot) -> str:
    """「命运之轮（逆位）」。

    大阿卡纳的原始牌名是「X 命运之轮 Wheel of Fortune」，序号与英文名
    在半宽的信息栏里会把标题挤成三行，卡片上只保留中文牌名。
    """
    full = (tarot.name or "").strip()
    name = fortune_grade.major_name(full) or full
    if not name:
        return ""
    return f"{name}（{tarot.position}）"


# ---------------------------------------------------------------- 文字


def render_text(fortune) -> str:
    """T2I 不可用时的降级文本。与图片内容一一对应。"""
    lines = [f"【P5X 每日运势】{fortune.day_label}", ""]
    lines.append(f"今日角色：{character_line(fortune)}")

    tarot = fortune.tarot
    if tarot.name:
        lines.append(f"塔罗指引：{tarot_label(tarot)}· {fortune.grade}")
        suitable, avoid = fortune.guide
        lines.append(f"  宜：{suitable}")
        lines.append(f"  忌：{avoid}")
        lines.append("")
        lines.append(f"综合建议：{fortune.advice}")

    lines.append("")
    lines.append(ENTERTAINMENT_NOTE)
    return "\n".join(lines)


# ---------------------------------------------------------------- 卡片


def _tarot_block(fortune) -> cards.Block:
    """塔罗是这张卡的第二主角：标题放牌名 + 正逆位 + 吉凶，正文放宜忌。

    牌义不在这里重复 —— 它已经写进「综合建议」那句话里了。
    """
    suitable, avoid = fortune.guide
    body = [f"宜：{suitable}", f"忌：{avoid}"]
    label = tarot_label(fortune.tarot) or "塔罗牌"
    return cards.Block(kind="item", lines=(f"· {label}· {fortune.grade}", *body))


def _tarot_section(fortune) -> cards.Section:
    return cards.Section(heading="塔罗指引", blocks=(_tarot_block(fortune),))


def _advice_section(fortune) -> cards.Section:
    return cards.Section(
        heading="综合建议",
        blocks=(cards.Block(kind="para", lines=(fortune.advice,)),),
    )


def build_sections(fortune) -> tuple[cards.Section, ...]:
    """两块：塔罗（评级 + 宜忌）与综合建议（结合牌义展开）。"""
    return (_tarot_section(fortune), _advice_section(fortune))


def build_page(fortune) -> cards.CardPage:
    """一张卡片。角色名是最大字号，所以放在 title；属性定位放 subtitle。"""
    character = fortune.character
    hero_key = (character.slug or "").strip() or character.key
    return cards.CardPage(
        category=CATEGORY,
        title=_short(character.name),
        subtitle=character.subtitle,
        # 卡片标题就是今天抽到的角色，导语只留日期；「今日角色」交给类别标签
        lead=fortune.day_label,
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
    "tarot_label",
]
