"""查询层门面：把「定位数据库 → 检索 → 取数 → 渲染」串成一个可线程调用的对象。

AstrBot 的事件循环里不能跑阻塞 SQL，所以每次查询都通过
``asyncio.to_thread`` 调到本模块；连接用一把锁串行化，
避免同一个 sqlite3 连接被多线程并发使用。
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import notices, renderer
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


class ArchiveService:
    """一个只读连接 + 一把锁 + 一组渲染好的查询方法。"""

    def __init__(self, database_path: str | Path, max_chars: int = DEFAULT_MAX_CHARS):
        self.database_path = Path(database_path).expanduser().resolve()
        self.max_chars = max(200, int(max_chars))
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
    ) -> "ArchiveService":
        """按配置顺序定位数据库。找不到会抛 DatabaseMissingError。"""
        path = resolve_database_path(configured_path, data_dir)
        return cls(path, max_chars=max_chars)

    # ---- 内部 ----

    def _resolve(self, term: str | None) -> tuple[SearchResult, str]:
        normalized = (term or "").strip()
        if not normalized:
            return SearchResult(character=None, candidates=(), term=""), normalized
        return search_characters(self._repo, normalized), normalized

    def _profile(self, character: Character, scope):
        return self._repo.build_profile(character, scope)

    # ---- 对外查询 ----

    def character_message(self, term: str | None) -> str:
        with self._lock:
            result, normalized = self._resolve(term)
            if not normalized:
                return renderer.render_usage("/p5x角色", "汐见琴音")
            if result.kind == "none":
                return renderer.render_not_found(normalized)
            if result.kind == "many":
                return renderer.render_multiple(result)
            profile = self._profile(result.character, CONFLICT_SCOPE_CHARACTER)
            return renderer.render_character(profile, self.max_chars)

    def skills_message(self, term: str | None) -> str:
        with self._lock:
            result, normalized = self._resolve(term)
            if not normalized:
                return renderer.render_usage("/p5x技能", "汐见琴音")
            if result.kind == "none":
                return renderer.render_not_found(normalized)
            if result.kind == "many":
                return renderer.render_multiple(result)
            profile = self._profile(result.character, CONFLICT_SCOPE_SKILL)
            return renderer.render_skills(profile, self.max_chars)

    def weapons_message(self, term: str | None) -> str:
        with self._lock:
            result, normalized = self._resolve(term)
            if not normalized:
                return renderer.render_usage("/p5x武器", "汐见琴音")
            if result.kind == "none":
                return renderer.render_not_found(normalized)
            if result.kind == "many":
                return renderer.render_multiple(result)
            profile = self._profile(result.character, CONFLICT_SCOPE_WEAPON)
            return renderer.render_weapons(profile, self.max_chars)

    def character_list_message(self) -> str:
        with self._lock:
            characters = self._repo.list_characters()
            return renderer.render_character_list(characters, self.max_chars)

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
    "notices",
    "renderer",
    "DEFAULT_MAX_CHARS",
]
