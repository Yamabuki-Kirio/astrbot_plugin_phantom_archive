"""查询层门面：把「定位数据库 → 检索 → 取数 → 渲染」串成一个可线程调用的对象。

AstrBot 的事件循环里不能跑阻塞 SQL，所以每次查询都通过
``asyncio.to_thread`` 调到本模块；连接用一把锁串行化，
避免同一个 sqlite3 连接被多线程并发使用。

每个查询都返回 :class:`QueryOutcome`：
    text  —— 文字版本，永远可用（也是图片渲染失败时的降级内容）
    pages —— 图片卡片的结构化模型；出错时为空，主程序据此决定发图还是发文字

这一层不生成 HTML、不发 HTTP 请求，图片相关的工作全部交给上层。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path

from . import cards, image_presenter, notices, panel_review, recommendation, renderer, yituliou
from .database import (
    ArchiveDatabase,
    ArchiveError,
    DatabaseMissingError,
    SchemaMismatchError,
    resolve_database_path,
)
from .models import Character, SearchResult
from .repository import (
    CONFLICT_SCOPE_CHARACTER,
    CONFLICT_SCOPE_SKILL,
    CONFLICT_SCOPE_WEAPON,
    ArchiveRepository,
)
from .search import search_characters, search_characters_exact

DEFAULT_MAX_CHARS = 1200

# 图片卡片的默认分页参数，可由插件配置覆盖
DEFAULT_ROWS_PER_PAGE = cards.DEFAULT_ROWS_PER_PAGE
DEFAULT_MAX_PAGES = cards.DEFAULT_MAX_PAGES

# 攻略类型 → 触发它的指令，用于「用法提示」里给出正确示例
GUIDE_COMMANDS: dict[str, str] = {
    "review": "/p5x评价",
    "setting": "/p5x养成",
    "operation": "/p5x操作",
    "party": "/p5x配队",
    "ritual": "/p5x意识",
    "innate": "/p5x属性意识",
}

ALL_GUIDE_TYPES: tuple[str, ...] = ("review", "setting", "operation", "party", "ritual", "innate")


@dataclass(frozen=True)
class QueryOutcome:
    """一次查询的结果。

    ``ok`` 为真表示查到了唯一角色，可以出图；为假时只有 ``text`` 有意义，
    内容就是「用法提示 / 没找到 / 候选过多」这几类提示。
    """

    ok: bool
    text: str
    pages: tuple[cards.CardPage, ...] = ()
    error_kind: str = ""  # usage | not_found | ambiguous | empty

    @property
    def has_pages(self) -> bool:
        return bool(self.pages)


def _error(text: str, kind: str) -> QueryOutcome:
    return QueryOutcome(ok=False, text=text, error_kind=kind)


class ArchiveService:
    """一个只读连接 + 一把锁 + 一组渲染好的查询方法。"""

    def __init__(
        self,
        database_path: str | Path,
        max_chars: int = DEFAULT_MAX_CHARS,
        rows_per_page: int = DEFAULT_ROWS_PER_PAGE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.max_chars = max(200, int(max_chars))
        self.rows_per_page = max(4, int(rows_per_page))
        self.max_pages = max(1, int(max_pages))
        self._lock = threading.Lock()
        self._db = ArchiveDatabase(self.database_path)
        self._repo = ArchiveRepository(self._db)
        self._yituliou = yituliou.load_repository()

    # ---- 构造 ----

    @classmethod
    def open(
        cls,
        configured_path: str | None,
        data_dir: str | Path,
        max_chars: int = DEFAULT_MAX_CHARS,
        rows_per_page: int = DEFAULT_ROWS_PER_PAGE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> "ArchiveService":
        """按配置顺序定位数据库。找不到会抛 DatabaseMissingError。"""
        path = resolve_database_path(configured_path, data_dir)
        return cls(path, max_chars=max_chars, rows_per_page=rows_per_page, max_pages=max_pages)

    # ---- 内部 ----

    def _resolve(self, term: str | None) -> tuple[SearchResult, str]:
        normalized = (term or "").strip()
        if not normalized:
            return SearchResult(character=None, candidates=(), term=""), normalized
        return search_characters(self._repo, normalized), normalized

    def _profile(self, character: Character, scope):
        return self._repo.build_profile(character, scope)

    def _with_recommendation(self, profile):
        """把 setting 攻略里的技能加点顺序解析成结构化推荐。

        解析放在查询层：模板不该分析攻略 JSON，展示层也不该回库。
        没有 setting 数据时返回 UNAVAILABLE，由展示层写「暂未收录」。
        """
        guide = next(
            (
                item
                for item in self._repo.build_guide_profile(profile.character, ("setting",)).guides
                if item.guide_type == "setting"
            ),
            None,
        )
        if guide is None:
            return profile
        parsed = recommendation.parse(
            guide.content,
            profile.skills,
            source_status=guide.source_status or "",
            translation_status=guide.translation_status or "",
        )
        return replace(profile, recommendation=parsed)

    def _paginate(self, builder, *args) -> tuple:
        return builder(*args, rows_per_page=self.rows_per_page, max_pages=self.max_pages)

    def _locate(self, term: str | None, command: str) -> tuple[SearchResult, str] | QueryOutcome:
        """解析检索词；拿不到唯一角色时直接返回错误结果。"""
        result, normalized = self._resolve(term)
        if not normalized:
            return _error(renderer.render_usage(command, "汐见琴音"), "usage")
        if result.kind == "none":
            return _error(renderer.render_not_found(normalized), "not_found")
        if result.kind == "many":
            return _error(renderer.render_multiple(result), "ambiguous")
        return result, normalized

    # ---- 对外查询：结构化结果 ----

    def _locate_yituliou_mode(self, term: str) -> tuple[yituliou.YituliouEntry, ...]:
        """仅当输入显式包含模式后缀（如「双子反抗」「J&C救援」）时直接命中一图流模式。"""
        base, mode_hint = yituliou._extract_mode_hint(term)
        if not mode_hint:
            return ()
        direct = self._yituliou.search(term)
        if direct:
            return direct
        result = search_characters(self._repo, base)
        if result.kind == "one" and result.character and result.character.slug:
            entry = self._yituliou.find_by_slug(result.character.slug, mode=mode_hint)
            if entry is not None:
                return (entry,)
        return ()

    def character_outcome(self, term: str | None, *, prefer_yituliou: bool = True) -> QueryOutcome:
        with self._lock:
            normalized = (term or "").strip()
            if not normalized:
                return _error(renderer.render_usage("/p5x角色", "汐见琴音"), "usage")

            if prefer_yituliou:
                mode_matches = self._locate_yituliou_mode(normalized)
                if len(mode_matches) == 1:
                    entry = mode_matches[0]
                    text = yituliou.render_yituliou_text(entry, self.max_chars)
                    pages = image_presenter.yituliou_character_pages(entry)
                    return QueryOutcome(ok=True, text=text, pages=pages)

            located = self._locate(normalized, "/p5x角色")
            if isinstance(located, QueryOutcome):
                return located

            character = located[0].character
            if prefer_yituliou:
                slug = (character.slug or "").strip()
                y_matches = self._yituliou.find_all_by_slug(slug) if slug else ()
                if len(y_matches) == 1:
                    entry = y_matches[0]
                    text = yituliou.render_yituliou_text(entry, self.max_chars)
                    pages = image_presenter.yituliou_character_pages(entry)
                    return QueryOutcome(ok=True, text=text, pages=pages)
                if len(y_matches) > 1:
                    lines = [f"{character.display_name}收录了 {len(y_matches)} 种一图流模式，请指定模式查询："]
                    for idx, item in enumerate(y_matches, start=1):
                        short_mode = item.mode.replace("模式", "")
                        lines.append(f"{idx}. /p5x角色 {character.display_name}{short_mode}（或 /p5x角色 双子{short_mode}）")
                    return _error("\n".join(lines), "ambiguous")

            profile = self._with_recommendation(
                self._profile(character, CONFLICT_SCOPE_CHARACTER)
            )
            text = renderer.render_character(profile, self.max_chars)
            pages = self._paginate(image_presenter.character_pages, profile)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

    def table_outcome(self, term: str | None = None) -> QueryOutcome:
        """查询一图流附表（留空列出全部 6 张附表）。"""
        with self._lock:
            normalized = (term or "").strip()
            tables = self._yituliou.tables
            if not normalized:
                return _error(yituliou.render_table_list_text(tables), "usage")
            table = None
            if normalized.isdigit():
                idx = int(normalized) - 1
                if 0 <= idx < len(tables):
                    table = tables[idx]
            if table is None:
                table = self._yituliou.find_table(normalized)
            if table is None:
                return _error(
                    f"未找到附表“{normalized}”。\n\n" + yituliou.render_table_list_text(tables),
                    "not_found",
                )
            text = yituliou.render_table_text(table)
            pages = image_presenter.yituliou_table_pages(table)
            return QueryOutcome(ok=True, text=text, pages=pages)

    def review_panel_image(self, image_bytes: bytes) -> QueryOutcome | None:
        """主动识图入口：非 P5X 角色面板截图返回 None；命中时返回单图点评结果。"""
        with self._lock:
            all_chars = self._repo.list_characters()
            extra_chars = tuple(
                ((c.slug or "").strip(), c.display_name)
                for c in all_chars
                if c.display_name
            )
            by_slug = {(c.slug or "").strip(): c for c in all_chars if c.slug}
        review = panel_review.analyze_panel_image(
            image_bytes,
            repo=self._yituliou,
            extra_characters=extra_chars,
        )
        if review is None:
            return None

        # 从数据库补充该角色已解锁意识（命座）中的局内暴击/穿透加成，防止 LLM 误判站街暴击率
        char_obj = by_slug.get(review.parsed.character_slug)
        if char_obj is not None:
            try:
                with self._lock:
                    gp = self._repo.build_guide_profile(char_obj, ("ritual",))
                ritual_hints: list[str] = []
                max_aw = review.parsed.awareness if review.parsed.awareness is not None else 6
                for g in gp.guides:
                    content = g.content if isinstance(g.content, dict) else {}
                    for k, v in content.items():
                        text_v = str(v or "").strip()
                        if not text_v or not any(kw in text_v for kw in ("暴击", "穿透", "破防")):
                            continue
                        digits = "".join(ch for ch in str(k) if ch.isdigit())
                        if digits and int(digits) > max_aw:
                            continue
                        short = text_v if len(text_v) <= 80 else text_v[:79] + "…"
                        ritual_hints.append(f"意识{digits or k}：{short}")
                if ritual_hints:
                    review = replace(review, in_battle_hints=tuple(ritual_hints[:3]))
            except Exception:  # noqa: BLE001
                pass

        text = panel_review.render_review_text(review)
        pages = image_presenter.panel_review_pages(review)
        return QueryOutcome(ok=True, text=text, pages=pages)

    def skills_outcome(self, term: str | None) -> QueryOutcome:
        with self._lock:
            located = self._locate(term, "/p5x技能")
            if isinstance(located, QueryOutcome):
                return located
            profile = self._profile(located[0].character, CONFLICT_SCOPE_SKILL)
            text = renderer.render_skills(profile, self.max_chars)
            pages = self._paginate(image_presenter.skills_pages, profile)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

    def weapons_outcome(self, term: str | None) -> QueryOutcome:
        with self._lock:
            located = self._locate(term, "/p5x武器")
            if isinstance(located, QueryOutcome):
                return located
            profile = self._profile(located[0].character, CONFLICT_SCOPE_WEAPON)
            text = renderer.render_weapons(profile, self.max_chars)
            pages = self._paginate(image_presenter.weapons_pages, profile)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

    def character_list_outcome(self) -> QueryOutcome:
        with self._lock:
            characters = self._repo.list_characters()
            text = renderer.render_character_list(characters, self.max_chars)
            if not characters:
                return _error(text, "empty")
            pages = self._paginate(image_presenter.character_list_pages, characters)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

    def guide_outcome(
        self, term: str | None, guide_type: str, *, prefer_yituliou: bool = True
    ) -> QueryOutcome:
        if guide_type not in GUIDE_COMMANDS:
            raise ValueError(f"不支持的攻略类型：{guide_type}")
        with self._lock:
            normalized = (term or "").strip()
            if not normalized:
                return _error(renderer.render_usage(GUIDE_COMMANDS[guide_type], "汐见琴音"), "usage")

            if prefer_yituliou and guide_type in ("review", "setting", "operation", "party"):
                y_entry = None
                mode_matches = self._locate_yituliou_mode(normalized)
                if len(mode_matches) == 1:
                    y_entry = mode_matches[0]
                else:
                    located_probe = self._locate(normalized, GUIDE_COMMANDS[guide_type])
                    if isinstance(located_probe, QueryOutcome):
                        return located_probe
                    slug = (located_probe[0].character.slug or "").strip()
                    y_matches = self._yituliou.find_all_by_slug(slug) if slug else ()
                    if len(y_matches) == 1:
                        y_entry = y_matches[0]
                if y_entry is not None:
                    raw_sections = yituliou.yituliou_guide_sections(y_entry, guide_type)
                    if raw_sections:
                        guide_title = renderer.GUIDE_TITLES.get(guide_type, guide_type)
                        text = yituliou.render_yituliou_guide_text(y_entry, guide_title, raw_sections)
                        card_sections = tuple(
                            cards.Section(
                                heading=heading,
                                blocks=tuple(cards.classify_block([line]) for line in body),
                            )
                            for heading, body in raw_sections
                        )
                        subtitle = renderer.join_meta(
                            [y_entry.role or None, "/".join(y_entry.elements) if y_entry.elements else None]
                        )
                        pages = cards.paginate(
                            guide_title,
                            y_entry.display_title,
                            card_sections,
                            subtitle=subtitle,
                            rows_per_page=self.rows_per_page,
                            max_pages=self.max_pages,
                            hero_kind=image_presenter.HERO_CHARACTER,
                            hero_key=y_entry.slug,
                            owner_key=y_entry.slug,
                        )
                        return QueryOutcome(ok=True, text=text, pages=tuple(pages))

            located = self._locate(normalized, GUIDE_COMMANDS[guide_type])
            if isinstance(located, QueryOutcome):
                return located
            profile = self._repo.build_guide_profile(located[0].character, (guide_type,))
            text = renderer.render_guide(profile, guide_type, self.max_chars)
            pages = self._paginate(image_presenter.guide_pages, profile, guide_type)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

    def all_guides_outcome(self, term: str | None) -> QueryOutcome:
        with self._lock:
            located = self._locate(term, "/p5x攻略")
            if isinstance(located, QueryOutcome):
                return located
            profile = self._repo.build_guide_profile(located[0].character, ALL_GUIDE_TYPES)
            text = renderer.render_all_guides(profile, self.max_chars)
            pages = self._paginate(image_presenter.all_guides_pages, profile)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

    # ---- 对外查询：纯文字（保留给测试与不支持图片的场景） ----

    def character_message(self, term: str | None) -> str:
        return self.character_outcome(term).text

    def skills_message(self, term: str | None) -> str:
        return self.skills_outcome(term).text

    def weapons_message(self, term: str | None) -> str:
        return self.weapons_outcome(term).text

    def character_list_message(self) -> str:
        return self.character_list_outcome().text

    def guide_message(self, term: str | None, guide_type: str) -> str:
        return self.guide_outcome(term, guide_type).text

    def all_guides_message(self, term: str | None) -> str:
        return self.all_guides_outcome(term).text

    # ---- 只读自检 ----

    def list_characters(self) -> tuple:
        """全部角色，按 source_key 稳定排序。

        每日运势要用它当候选池：**必须**按稳定键排序，
        否则 SQLite 换个查询计划、库被 VACUUM 过，抽到的人就变了。
        """
        with self._lock:
            characters = self._repo.list_characters()
        return tuple(sorted(characters, key=lambda item: item.source_key or ""))

    def exact_character(self, term: str | None) -> SearchResult:
        """Resolve an exact name or registered alias without fuzzy fallback."""
        return search_characters_exact(self.list_characters(), term)

    def count_characters(self) -> int:
        with self._lock:
            return int(self._db.scalar("SELECT COUNT(*) FROM zh_characters", default=0))

    def close(self) -> None:
        with self._lock:
            self._db.close()


__all__ = [
    "ArchiveService",
    "ArchiveError",
    "DatabaseMissingError",
    "SchemaMismatchError",
    "QueryOutcome",
    "GUIDE_COMMANDS",
    "ALL_GUIDE_TYPES",
    "notices",
    "renderer",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_ROWS_PER_PAGE",
    "DEFAULT_MAX_PAGES",
]
