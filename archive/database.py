"""只读数据库连接与结构校验。

三层防护保证不会写库：
1. URI 参数 ``mode=ro``：SQLite 层直接拒绝写入。
2. 连接后执行 ``PRAGMA query_only = ON``：即便有人拼错 URI，写入也会报错。
3. 本模块只暴露 ``fetch_all`` / ``fetch_one``，没有 execute 之外的写入口。

插件只读 ``p5x_zh.sqlite3``，绝不碰原始库 ``p5x.sqlite3``。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

logger = logging.getLogger(__name__)

DATABASE_FILENAME = "p5x_zh.sqlite3"
NOTICES_FILENAME = "display_notices.json"

# 原始库文件名，出现即拒绝，避免误配
FORBIDDEN_FILENAMES = ("p5x.sqlite3", "p5x.sqlite3-wal", "p5x.sqlite3-shm")

# 初始化时校验的必需表与必需字段（§14）
REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "zh_characters": (
        "source_key", "name_zh", "codename_zh", "slug", "element_zh",
        "position_zh", "rarity", "description_zh", "source_status", "translation_status",
    ),
    "zh_skills": (
        "character_key", "skill_key", "name_zh", "element_zh", "type_zh",
        "description_zh", "sp", "cooldown", "translation_status",
    ),
    "zh_weapons": (
        "character_key", "weapon_key", "name_zh", "rarity", "health",
        "attack", "defense", "skill_name_zh", "description_zh", "translation_status",
    ),
    "zh_guides": (
        "character_key", "guide_type", "content_json", "source_status",
        "translation_status", "source_url",
    ),
    "numeric_conflicts": ("character_key", "resource_type", "numeric_conflict"),
}


class ArchiveError(Exception):
    """插件内部异常的基类，便于上层统一兜底。"""


class DatabaseMissingError(ArchiveError):
    """数据库文件不存在，或路径指向了不允许的文件。"""


class SchemaMismatchError(ArchiveError):
    """数据库存在但结构不兼容。"""


def resolve_database_path(configured: str | None, data_dir: str | Path) -> Path:
    """按「配置绝对路径 → 插件 data 目录」的顺序定位数据库（§5）。

    找不到就抛错，绝不静默退回原始库，也不自动下载。
    """
    candidates: list[Path] = []
    if configured and str(configured).strip():
        candidates.append(Path(str(configured).strip()).expanduser())
    candidates.append(Path(data_dir) / DATABASE_FILENAME)

    for candidate in candidates:
        if candidate.name in FORBIDDEN_FILENAMES:
            raise DatabaseMissingError(
                f"配置指向了原始数据库 {candidate.name}，插件只允许读取 {DATABASE_FILENAME}"
            )
        if candidate.is_file():
            return candidate

    tried = "、".join(str(p) for p in candidates)
    raise DatabaseMissingError(f"数据库文件不存在，已尝试：{tried}")


def build_uri(path: str | Path) -> str:
    """构造只读 URI。中文路径需要百分号编码，否则 sqlite3 打不开。"""
    absolute = Path(path).expanduser().resolve()
    posix = absolute.as_posix()
    # 盘符与分隔符保留，其余（含中文、空格、#、?）全部编码
    return f"file:{quote(posix, safe='/:')}?mode=ro"


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """以只读方式打开数据库，并关闭多线程检查供线程池调用。"""
    uri = build_uri(path)
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5.0)
    except sqlite3.Error as exc:
        raise DatabaseMissingError(f"无法打开数据库：{exc}") from exc

    conn.row_factory = sqlite3.Row
    # 第二层保险：即使 URI 写错也不允许写
    conn.execute("PRAGMA query_only = ON")
    return conn


def validate_schema(conn: sqlite3.Connection) -> None:
    """校验必需表与必需字段，缺任何一项都视为版本不兼容（§14）。"""
    existing_tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    problems: list[str] = []

    for table, columns in REQUIRED_SCHEMA.items():
        if table not in existing_tables:
            problems.append(f"缺少表 {table}")
            continue
        actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = [c for c in columns if c not in actual]
        if missing:
            problems.append(f"{table} 缺少字段 {'/'.join(missing)}")

    if problems:
        raise SchemaMismatchError("；".join(problems))


class ArchiveDatabase:
    """只读查询入口。所有 SQL 都走参数化，不接受拼接字符串。"""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._conn = open_readonly(self.path)
        try:
            validate_schema(self._conn)
        except Exception:
            self._conn.close()
            raise

    # ---- 查询 ----

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, tuple(params)).fetchall()

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, tuple(params)).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        row = self.fetch_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def executemany_guard(self, statements: Iterable[str]) -> None:  # pragma: no cover
        """占位：明确表达本类不提供批量写接口，调用即报错。"""
        raise ArchiveError("夜幕档案为只读查询层，不支持写入")

    # ---- 生命周期 ----

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover
            pass

    def __enter__(self) -> "ArchiveDatabase":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
