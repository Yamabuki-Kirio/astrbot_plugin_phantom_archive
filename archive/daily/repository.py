"""每日运势持久化：独立的 ``p5x_daily.sqlite3``。

为什么不写进 ``p5x_zh.sqlite3``：那份库是只读资料库，
「只读」是插件的底线，为了记一条运势就给它加表，等于把底线拆了。
所以这里另开一个库，专门存「谁在哪天抽到了什么」。

并发策略（多人同时第一次查同一天）：

1. 先 ``SELECT``，命中就直接返回；
2. 没命中就生成，然后 ``INSERT OR IGNORE``；
3. 无论插入成功与否，再 ``SELECT`` 一次读回库里那一行。

这样两个人同时抢着写，最终两人看到的都是**先落库的那一份**，
不会出现「同一天两份不同结果」。唯一约束
``(date_key, timezone, platform_id, user_id)`` 是这条保证的地基。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DAILY_DB_FILENAME = "p5x_daily.sqlite3"
TABLE_NAME = "daily_fortunes"
SCHEMA_VERSION = 1

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    date_key      TEXT NOT NULL,
    timezone      TEXT NOT NULL,
    platform_id   TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    character_key TEXT NOT NULL,
    fortune_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (date_key, timezone, platform_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_character
    ON {TABLE_NAME} (character_key);
"""

# 只允许这四个字段参与定位，顺序固定，避免调用方拼错
KEY_COLUMNS = ("date_key", "timezone", "platform_id", "user_id")


class DailyStoreError(Exception):
    """每日库不可用。上层据此决定是否仍然返回结果。"""


@dataclass(frozen=True)
class DailyRecord:
    """库里的一行。``payload`` 是已经反序列化的 ``fortune_json``。"""

    date_key: str
    timezone: str
    platform_id: str
    user_id: str
    display_name: str
    character_key: str
    payload: dict
    created_at: str

    @property
    def identity(self) -> dict:
        return {
            "date_key": self.date_key,
            "timezone": self.timezone,
            "platform_id": self.platform_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
        }


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DailyFortuneStore:
    """每日运势库。写入是「先查、再忽略式插入、再查」，见模块文档。"""

    def __init__(self, path: str | Path, *, timeout: float = 5.0):
        self.path = Path(path).expanduser()
        if self.path.name.endswith(("p5x.sqlite3", "p5x_zh.sqlite3")):
            raise DailyStoreError(
                f"每日运势库不能指向资料库（{self.path.name}），请使用 {DAILY_DB_FILENAME}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(
                str(self.path), timeout=timeout, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise DailyStoreError(f"每日运势库无法打开：{exc}") from exc

    # ---- 读 ----

    def get(
        self,
        *,
        date_key: str,
        timezone: str,
        platform_id: str,
        user_id: str,
    ) -> DailyRecord | None:
        sql = (
            f"SELECT * FROM {TABLE_NAME} WHERE date_key = ? AND timezone = ?"
            " AND platform_id = ? AND user_id = ?"
        )
        with self._lock:
            row = self._conn.execute(
                sql, (date_key, timezone, platform_id, user_id)
            ).fetchone()
        return _to_record(row) if row is not None else None

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE_NAME}").fetchone()
        return int(row["n"]) if row is not None else 0

    # ---- 写 ----

    def insert(
        self,
        *,
        date_key: str,
        timezone: str,
        platform_id: str,
        user_id: str,
        display_name: str,
        character_key: str,
        payload: dict,
    ) -> bool:
        """忽略式插入。返回 True 表示这次真的写进去了。"""
        sql = (
            f"INSERT OR IGNORE INTO {TABLE_NAME}"
            " (date_key, timezone, platform_id, user_id, display_name,"
            "  character_key, fortune_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            date_key,
            timezone,
            platform_id,
            user_id,
            display_name or "",
            character_key or "",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            _now_iso(),
        )
        try:
            with self._lock:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise DailyStoreError(f"每日运势写入失败：{exc}") from exc

    # ---- 生命周期 ----

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass

    def __enter__(self) -> "DailyFortuneStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _to_record(row: sqlite3.Row) -> DailyRecord:
    try:
        payload: Any = json.loads(row["fortune_json"])
    except (TypeError, json.JSONDecodeError):
        logger.warning("每日运势记录损坏，按空内容处理：%s", row["date_key"])
        payload = {}
    return DailyRecord(
        date_key=row["date_key"],
        timezone=row["timezone"],
        platform_id=row["platform_id"],
        user_id=row["user_id"],
        display_name=row["display_name"] or "",
        character_key=row["character_key"] or "",
        payload=payload if isinstance(payload, dict) else {},
        created_at=row["created_at"] or "",
    )


__all__ = [
    "DAILY_DB_FILENAME",
    "DailyFortuneStore",
    "DailyRecord",
    "DailyStoreError",
    "KEY_COLUMNS",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "TABLE_NAME",
]
