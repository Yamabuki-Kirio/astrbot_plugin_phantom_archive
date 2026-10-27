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
from dataclasses import dataclass
from pathlib import Path

from . import cards, image_presenter, notices, renderer
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
from .search import search_characters

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

    def character_outcome(self, term: str | None) -> QueryOutcome:
        with self._lock:
            located = self._locate(term, "/p5x角色")
            if isinstance(located, QueryOutcome):
                return located
            profile = self._profile(located[0].character, CONFLICT_SCOPE_CHARACTER)
            text = renderer.render_character(profile, self.max_chars)
            pages = self._paginate(image_presenter.character_pages, profile)
            return QueryOutcome(ok=True, text=text, pages=tuple(pages))

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

    def guide_outcome(self, term: str | None, guide_type: str) -> QueryOutcome:
        if guide_type not in GUIDE_COMMANDS:
            raise ValueError(f"不支持的攻略类型：{guide_type}")
        with self._lock:
            located = self._locate(term, GUIDE_COMMANDS[guide_type])
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
