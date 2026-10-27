"""模板层：把卡片展示模型变成一段完整 HTML。

这一层只做「数据 → HTML 字符串」：
- 不查数据库（数据由 service/presenter 提供）；
- 不发 HTTP 请求（发送由 image_renderer 负责）；
- 不允许模板里出现外链字体、CDN 或脚本 —— 渲染服务可能没有外网，
  而且外链资源会让出图时间变得不可控。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from . import cards, notices, visual_context

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# 卡片类别 → 模板文件。攻略类的 category 是「评价 / 养成 / …」这些中文标题，
# 它们共用同一套版式，只靠标题区分。
CATEGORY_TEMPLATES: dict[str, str] = {
    "角色": "character.html",
    "技能": "skill.html",
    "武器": "weapon.html",
    "角色列表": "character_list.html",
    "每日运势": "daily_fortune.html",
    "评价": "guide.html",
    "养成": "guide.html",
    "操作": "guide.html",
    "配队": "guide.html",
    "意识": "guide.html",
    "属性意识": "guide.html",
    "攻略": "guide.html",
}

FALLBACK_TEMPLATE = "character.html"

THEME_DARK = "dark"
THEME_LIGHT = "light"
THEMES: tuple[str, ...] = (THEME_DARK, THEME_LIGHT)

BULLET_PREFIX = "· "

_environment: Environment | None = None


def get_environment(template_dir: Path | None = None) -> Environment:
    """缓存一个 Jinja2 环境；模板目录变动只在重启后生效。"""
    global _environment
    if template_dir is not None:
        return _build_environment(Path(template_dir))
    if _environment is None:
        _environment = _build_environment(TEMPLATE_DIR)
    return _environment


def _build_environment(directory: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(directory)),
        autoescape=select_autoescape(enabled_extensions=("html", "htm"), default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )
    return env


def template_for(category: str) -> str:
    """类别 → 模板名；未知类别回落到通用档案版式而不是报错。"""
    name = CATEGORY_TEMPLATES.get(category)
    if name is not None:
        return name
    # 攻略类的中文标题会带「攻略」后缀（评价攻略 / 综合攻略 …），统一用攻略版式
    if category.endswith("攻略"):
        return "guide.html"
    logger.warning("未知卡片类别 %r，回退到 %s", category, FALLBACK_TEMPLATE)
    return FALLBACK_TEMPLATE


def available_templates() -> tuple[str, ...]:
    """磁盘上真实存在的模板文件，供 /p5x渲染状态 自检。"""
    directory = TEMPLATE_DIR
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.name for path in directory.glob("*.html")))


# ---------------------------------------------------------------- 上下文


def _clean(lines: Iterable[str]) -> list[str]:
    return [line for line in (item.strip() for item in lines) if line]


def _strip_bullet(line: str) -> str:
    return line[len(BULLET_PREFIX):].strip() if line.startswith(BULLET_PREFIX) else line


def block_context(block: cards.Block) -> dict:
    """把排版块转成模板可直接用的字典。

    ``·`` 前缀在这里就剥掉，模板不再做字符串裁剪 —— 模板越笨，
    用户数据被误当成标记处理的机会越少。
    """
    ref_key = getattr(block, "ref_key", "")
    if block.kind == "item":
        # 条目正文里也可能自带 · 前缀（原文就是这样排的），一并剥掉
        return {
            "kind": "item",
            "title": block.title,
            "lines": _clean(_strip_bullet(line) for line in block.body),
            "ref_key": ref_key,
        }
    if block.kind == "bullets":
        return {
            "kind": "bullets",
            "lines": _clean(_strip_bullet(line) for line in block.lines),
            "ref_key": ref_key,
        }
    if block.kind == "kv":
        return {"kind": "kv", "lines": _clean(block.lines), "ref_key": ref_key}
    return {"kind": "para", "lines": _clean(block.lines), "ref_key": ref_key}


def section_context(section: cards.Section) -> dict:
    blocks = [block_context(block) for block in section.blocks]
    return {
        "heading": section.heading,
        "blocks": [block for block in blocks if block["lines"] or block.get("title")],
    }


def build_context(
    page: cards.CardPage,
    *,
    theme: str = THEME_DARK,
    visual: visual_context.VisualContext | None = None,
) -> dict:
    """一张卡片的完整模板上下文。缺字段直接抛错，由调用方降级成文字。"""
    if theme not in THEMES:
        theme = THEME_DARK
    visual_dict = (visual or visual_context.EMPTY_VISUAL).as_template_dict()
    return {
        "theme": theme,
        "category": page.category,
        "title": page.title,
        "subtitle": page.subtitle,
        "lead": page.lead,
        "page_index": page.page_index,
        "page_total": page.page_total,
        "page_label": page.page_label,
        "sections": [section_context(section) for section in page.sections],
        "omitted": page.omitted,
        "show_footer": page.show_footer,
        "has_conflict": page.has_conflict,
        "conflict_warning": notices.NUMERIC_CONFLICT_WARNING,
        "source_label": page.source_label,
        "translation_label": page.translation_label,
        "disclaimer_lines": page.disclaimer_lines or notices.DISCLAIMER_LINES,
        "visual": visual_dict,
    }


# ---------------------------------------------------------------- 渲染


def render_page(
    page: cards.CardPage,
    *,
    theme: str = THEME_DARK,
    visual: visual_context.VisualContext | None = None,
) -> str:
    """渲染一张卡片。visual 为空时输出无头像、无立绘的纯色卡片。"""
    env = get_environment()
    template = env.get_template(template_for(page.category))
    return template.render(**build_context(page, theme=theme, visual=visual))


def render_pages(
    pages: Sequence[cards.CardPage],
    *,
    theme: str = THEME_DARK,
    visuals: Sequence[visual_context.VisualContext | None] | None = None,
) -> list[str]:
    if visuals is None:
        return [render_page(page, theme=theme) for page in pages]
    return [
        render_page(page, theme=theme, visual=visual)
        for page, visual in zip(pages, visuals)
    ]


__all__ = [
    "TEMPLATE_DIR",
    "CATEGORY_TEMPLATES",
    "THEMES",
    "THEME_DARK",
    "THEME_LIGHT",
    "template_for",
    "available_templates",
    "build_context",
    "block_context",
    "section_context",
    "render_page",
    "render_pages",
    "get_environment",
]
