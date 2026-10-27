"""卡片展示模型：把「一组排版块」变成可分页、可渲染成 HTML 的结构。

这一层不碰数据库，也不碰 HTTP。它只回答两个问题：
1. 一段文本块该被当成标题、条目、列表还是键值对；
2. 超过一页的容量时，怎么按整块切分到多页。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

# 一页大约能放多少行（按 760–900px 宽、正文 15px 估算）
DEFAULT_ROWS_PER_PAGE = 28
DEFAULT_MAX_PAGES = 4

# 每行大约能放多少汉字，用于估算折行
CHARS_PER_ROW = 42

HEADING_PREFIX = "■ "
BULLET_PREFIX = "· "
CONTINUATION_PREFIX = "  "


@dataclass(frozen=True)
class Block:
    """一个排版块。kind 决定 HTML 用哪种样式，lines 保留原始文本。

    ``ref_key`` 是这块内容指向的业务标识（目前只有武器块会填），
    视觉层用它去查缩略图；它只是标识符，不带路径也不带 URL。
    """

    kind: str  # item | para | bullets | kv
    lines: tuple[str, ...]
    ref_key: str = ""

    @property
    def title(self) -> str:
        if self.kind == "item":
            return self.lines[0][len(BULLET_PREFIX):].strip()
        return ""

    @property
    def body(self) -> tuple[str, ...]:
        if self.kind == "item":
            return tuple(line.strip() for line in self.lines[1:])
        return self.lines

    @property
    def rows(self) -> int:
        """估算占用的行数，用来分页。"""
        if self.kind == "bullets":
            return sum(max(1, math.ceil(len(line) / CHARS_PER_ROW)) for line in self.lines)
        if self.kind == "item":
            body = sum(max(1, math.ceil(len(line) / CHARS_PER_ROW)) for line in self.body)
            return 1 + body
        return max(1, math.ceil(sum(len(line) for line in self.lines) / CHARS_PER_ROW))


@dataclass(frozen=True)
class Section:
    heading: str
    blocks: tuple[Block, ...]

    @property
    def rows(self) -> int:
        return 1 + sum(block.rows for block in self.blocks)


@dataclass(frozen=True)
class CardPage:
    """一张图片对应的内容。"""

    category: str  # 角色 / 技能 / 意识 …
    title: str
    subtitle: str = ""
    lead: str = ""
    sections: tuple[Section, ...] = ()
    page_index: int = 1
    page_total: int = 1
    omitted: int = 0  # 因页数上限没展示的条目数
    source_label: str = ""
    translation_label: str = ""
    has_conflict: bool = False
    show_footer: bool = False  # 最后一张才放免责声明
    # 视觉层需要的业务标识，不含任何路径或 URL
    hero_kind: str = ""  # character | weapon | archive
    hero_key: str = ""  # 角色 slug / 武器 key
    owner_key: str = ""  # 武器缺图时回退到哪个角色

    @property
    def page_label(self) -> str:
        if self.page_total <= 1:
            return ""
        return f"{self.page_index}/{self.page_total}"

    @property
    def has_content(self) -> bool:
        return bool(self.sections) or bool(self.lead)


def classify_block(lines: list[str]) -> Block:
    """把一组原始文本行归类。

    分类只依赖 ``■`` / ``·`` 前缀和续行缩进 —— 这些格式由 renderer 的
    块生成函数固定产出，不会漂移；有测试兜底。
    """
    cleaned = [line for line in lines if line is not None]
    if not cleaned:
        return Block(kind="para", lines=("",))

    if len(cleaned) == 1:
        line = cleaned[0]
        if line.startswith(BULLET_PREFIX):
            return Block(kind="bullets", lines=(line,))
        if "：" in line and len(line) <= 120:
            return Block(kind="kv", lines=(line,))
        return Block(kind="para", lines=(line,))

    if cleaned[0].startswith(BULLET_PREFIX):
        # 全部都是「· 」开头的，就是一组并列条目，交给列表样式
        if all(line.startswith(BULLET_PREFIX) for line in cleaned):
            return Block(kind="bullets", lines=tuple(cleaned))
        # 首行带 · 、后面是缩进续行的，是「标题 + 正文」的条目块
        return Block(kind="item", lines=tuple(cleaned))

    # 混排时退化成段落，宁可样式简单也不要错标
    return Block(kind="para", lines=tuple(cleaned))


def split_sections(raw_blocks: list[list[str]]) -> tuple[Section, ...]:
    """把 ``["■ 优点", "· a", "· b"]`` 拆成「节标题 + 块」。"""
    sections: list[Section] = []
    heading = ""
    pending: list[Block] = []

    def flush() -> None:
        nonlocal heading, pending
        if pending or heading:
            sections.append(Section(heading=heading, blocks=tuple(pending)))
        heading, pending = "", []

    for raw in raw_blocks:
        if not raw:
            continue
        first = raw[0]
        if first.startswith(HEADING_PREFIX):
            flush()
            heading = first[len(HEADING_PREFIX):].strip()
            rest = raw[1:]
            if rest:
                pending.append(classify_block(list(rest)))
            continue
        pending.append(classify_block(list(raw)))

    flush()
    return tuple(section for section in sections if section.blocks or section.heading)


def paginate(
    category: str,
    title: str,
    sections: tuple[Section, ...],
    *,
    subtitle: str = "",
    lead: str = "",
    rows_per_page: int = DEFAULT_ROWS_PER_PAGE,
    max_pages: int = DEFAULT_MAX_PAGES,
    hero_kind: str = "",
    hero_key: str = "",
    owner_key: str = "",
) -> list[CardPage]:
    """按整块切页。

    绝不从块中间切开 —— 放不下的块整块挪到下一页；
    页数用尽后统计未展示条目数，交给模板显示「另有 N 项未展示」。
    """
    pages: list[list[Section]] = []
    current: list[Section] = []
    used = 0
    lead_rows = math.ceil(len(lead) / CHARS_PER_ROW) if lead else 0

    def push_page() -> None:
        nonlocal current, used
        pages.append(current)
        current = []
        used = 0

    for section in sections:
        remaining = list(section.blocks)
        first_chunk = True
        while remaining:
            # 首页要扣掉导语占的行；每节还要留 1 行给节标题
            base = rows_per_page if (pages or current) else rows_per_page - lead_rows
            limit = base - used - 1
            if limit <= 0:
                push_page()
                continue

            chunk: list[Block] = []
            chunk_rows = 0
            while remaining:
                block = remaining[0]
                if chunk and chunk_rows + block.rows > limit:
                    break
                chunk.append(remaining.pop(0))
                chunk_rows += block.rows

            if not chunk:
                # 单个块就超过整页：独占一页，宁可溢出也不丢内容
                chunk = [remaining.pop(0)]
                chunk_rows = limit

            heading = section.heading if first_chunk else f"{section.heading}（续）"
            current.append(Section(heading=heading, blocks=tuple(chunk)))
            used += chunk_rows + 1
            first_chunk = False

            if remaining:
                push_page()

    if current:
        pages.append(current)

    if not pages:
        pages = [[]]

    omitted = 0
    if len(pages) > max_pages:
        # 统计被砍掉的条目数，用户要知道自己少看了多少
        dropped = pages[max_pages - 1:]
        omitted = sum(len(section.blocks) for page in dropped for section in page)
        pages = pages[:max_pages]

    total = len(pages)
    result: list[CardPage] = []
    for index, page_sections in enumerate(pages, start=1):
        result.append(
            CardPage(
                category=category,
                title=title,
                subtitle=subtitle,
                lead=lead if index == 1 else "",
                sections=tuple(page_sections),
                page_index=index,
                page_total=total,
                omitted=omitted if index == total else 0,
                show_footer=index == total,
                hero_kind=hero_kind,
                hero_key=hero_key,
                owner_key=owner_key,
            )
        )
    return result


def with_hero(page: CardPage, *, kind: str, key: str, owner: str = "") -> CardPage:
    """给某一页指定主视觉。武器页要在分页之后才知道「本页第一把武器」是谁。"""
    return replace(page, hero_kind=kind, hero_key=key, owner_key=owner)


def with_footer(page: CardPage, *, source_label: str, translation_label: str, has_conflict: bool) -> CardPage:
    return replace(
        page,
        source_label=source_label,
        translation_label=translation_label,
        has_conflict=has_conflict,
    )
