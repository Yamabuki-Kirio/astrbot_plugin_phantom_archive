"""角色面板历史记录与成长对比：独立的 ``p5x_panel_history.sqlite3``。

遵循插件只读资料库底线：绝不写入 ``p5x_zh.sqlite3``，独立建库保存
「同一用户 + 同一角色」的历次面板快照，并在用户再次发送同一角色面板时，
自动对比综合评分、等级、命座、武器改造、启示卡、技能等级与各项属性变化，
判断是否有提升、无变化或回调。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

from . import panel_review

logger = logging.getLogger(__name__)

PANEL_DB_FILENAME = "p5x_panel_history.sqlite3"
TABLE_NAME = "panel_snapshots"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_id    TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    display_name   TEXT NOT NULL DEFAULT '',
    character_slug TEXT NOT NULL,
    character_name TEXT NOT NULL,
    level          INTEGER,
    awareness      INTEGER,
    weapon_name    TEXT NOT NULL DEFAULT '',
    weapon_level   INTEGER,
    weapon_mod     INTEGER,
    revelation_set TEXT NOT NULL DEFAULT '',
    total_score    INTEGER,
    grade          TEXT NOT NULL DEFAULT '',
    stats_json     TEXT NOT NULL DEFAULT '{{}}',
    skills_json    TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_user_char
    ON {TABLE_NAME} (platform_id, user_id, character_slug, id DESC);
"""

GRADE_RANK: dict[str, int] = {"C": 1, "B": 2, "A": 3, "S": 4}
SKILL_NAMES: tuple[str, ...] = ("①", "②", "③", "战斗技巧")


class PanelHistoryError(Exception):
    """面板历史库不可用。上层据此优雅降级，不影响点评返回。"""


@dataclass(frozen=True)
class PanelSnapshot:
    id: int
    platform_id: str
    user_id: str
    display_name: str
    character_slug: str
    character_name: str
    level: int | None
    awareness: int | None
    weapon_name: str
    weapon_level: int | None
    weapon_mod: int | None
    revelation_set: str
    total_score: int | None
    grade: str
    stats: dict[str, str]
    skill_levels: tuple[int, ...]
    created_at: str


@dataclass(frozen=True)
class PanelDelta:
    """本次面板与该用户上一次同角色面板的成长对比。"""

    status: str  # first | improved | same | regressed | adjusted
    headline: str
    improvements: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    neutral_changes: tuple[str, ...] = ()
    prev_created_at: str = ""
    record_count: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "headline": self.headline,
            "improvements": list(self.improvements),
            "regressions": list(self.regressions),
            "neutral_changes": list(self.neutral_changes),
            "prev_created_at": self.prev_created_at,
            "record_count": self.record_count,
        }


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_num(val: str) -> tuple[float | None, bool]:
    text = (val or "").strip()
    is_pct = "%" in text
    m = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return (float(m.group(1)), is_pct) if m else (None, is_pct)


def _fmt_delta(diff: float, is_pct: bool) -> str:
    sign = "+" if diff > 0 else ""
    num = (
        f"{diff:.1f}".rstrip("0").rstrip(".")
        if abs(diff - round(diff)) > 1e-6
        else f"{int(round(diff))}"
    )
    return f"{sign}{num}%" if is_pct else f"{sign}{num}"


def compare_panel(
    prev: PanelSnapshot,
    current: panel_review.ParsedPanel,
    current_grade: str,
    *,
    record_count: int = 2,
) -> PanelDelta:
    """量化对比本次面板与上一次同角色快照，判定是否有提升。"""
    improvements: list[str] = []
    regressions: list[str] = []
    neutral_changes: list[str] = []
    up_weight = 0
    down_weight = 0

    # 1. 综合评分
    if prev.total_score is not None and current.total_score is not None:
        diff_score = current.total_score - prev.total_score
        if diff_score >= 5:
            improvements.append(
                f"综合评分 {prev.total_score}→{current.total_score}（+{diff_score}）"
            )
            up_weight += 3
        elif diff_score <= -5:
            regressions.append(
                f"综合评分 {prev.total_score}→{current.total_score}（{diff_score}）"
            )
            down_weight += 3

    # 2. 角色意识（命座）
    if prev.awareness is not None and current.awareness is not None:
        diff_aw = current.awareness - prev.awareness
        if diff_aw > 0:
            improvements.append(
                f"意识（命座）{prev.awareness}命→{current.awareness}命（+{diff_aw}命）"
            )
            up_weight += 4
        elif diff_aw < 0:
            regressions.append(f"意识（命座）{prev.awareness}命→{current.awareness}命")
            down_weight += 2

    # 3. 角色等级
    if prev.level is not None and current.level is not None:
        diff_lv = current.level - prev.level
        if diff_lv > 0:
            improvements.append(f"角色等级 Lv.{prev.level}→Lv.{current.level}（+{diff_lv}）")
            up_weight += 2
        elif diff_lv < 0:
            regressions.append(f"角色等级 Lv.{prev.level}→Lv.{current.level}")
            down_weight += 2

    # 4. 武器名 / 武器等级 / 武器改造
    if prev.weapon_name and current.weapon_name and prev.weapon_name != current.weapon_name:
        neutral_changes.append(f"更换武器：{prev.weapon_name} → {current.weapon_name}")
    if prev.weapon_level is not None and current.weapon_level is not None:
        diff_wlv = current.weapon_level - prev.weapon_level
        if diff_wlv > 0:
            improvements.append(f"武器等级 Lv.{prev.weapon_level}→Lv.{current.weapon_level}")
            up_weight += 2
        elif diff_wlv < 0:
            regressions.append(f"武器等级 Lv.{prev.weapon_level}→Lv.{current.weapon_level}")
            down_weight += 2
    if prev.weapon_mod is not None and current.weapon_mod is not None:
        diff_mod = current.weapon_mod - prev.weapon_mod
        if diff_mod > 0:
            improvements.append(
                f"武器改造 {prev.weapon_mod}改→{current.weapon_mod}改（+{diff_mod}改）"
            )
            up_weight += 3
        elif diff_mod < 0:
            regressions.append(f"武器改造 {prev.weapon_mod}改→{current.weapon_mod}改")
            down_weight += 2

    # 5. 启示卡套装变更
    if prev.revelation_set and current.revelation_set and prev.revelation_set != current.revelation_set:
        neutral_changes.append(f"更换启示卡：{prev.revelation_set} → {current.revelation_set}")

    # 6. 技能等级变化
    if prev.skill_levels and current.skill_levels:
        skill_ups: list[str] = []
        skill_downs: list[str] = []
        for idx, (old_lv, new_lv) in enumerate(zip(prev.skill_levels, current.skill_levels)):
            if idx >= len(SKILL_NAMES):
                break
            if new_lv > old_lv:
                skill_ups.append(f"{SKILL_NAMES[idx]} Lv.{old_lv}→{new_lv}")
            elif new_lv < old_lv:
                skill_downs.append(f"{SKILL_NAMES[idx]} Lv.{old_lv}→{new_lv}")
        if skill_ups:
            improvements.append("技能提升：" + " / ".join(skill_ups))
            up_weight += 2
        if skill_downs:
            regressions.append("技能变动：" + " / ".join(skill_downs))
            down_weight += 1

    # 7. 面板属性数值对比（仅比对两次都成功识别到的共有属性）
    cur_stats = current.stat_map()
    for stat_name, new_str in cur_stats.items():
        if stat_name not in prev.stats:
            continue
        old_str = prev.stats[stat_name]
        old_num, old_pct = _parse_num(old_str)
        new_num, new_pct = _parse_num(new_str)
        if old_num is None or new_num is None or old_pct != new_pct:
            continue
        diff = new_num - old_num
        threshold = 0.5 if (new_pct or "." in old_str or "." in new_str) else 5.0
        if abs(diff) < threshold:
            continue
        delta_s = _fmt_delta(diff, new_pct)
        if diff > 0:
            improvements.append(f"{stat_name} {old_str}→{new_str}（{delta_s}）")
            up_weight += 1
        else:
            regressions.append(f"{stat_name} {old_str}→{new_str}（{delta_s}）")
            down_weight += 1

    # 8. 综合评级变化
    old_rank = GRADE_RANK.get(prev.grade, 0)
    new_rank = GRADE_RANK.get(current_grade, 0)
    if old_rank and new_rank:
        if new_rank > old_rank:
            improvements.append(f"练度评级 {prev.grade}→{current_grade}")
            up_weight += 3
        elif new_rank < old_rank:
            regressions.append(f"练度评级 {prev.grade}→{current_grade}")
            down_weight += 3

    if not improvements and not regressions and not neutral_changes:
        return PanelDelta(
            status="same",
            headline="与上次提交相比无明显变化（练度尚未提升）",
            prev_created_at=prev.created_at,
            record_count=record_count,
        )

    if up_weight > down_weight and improvements:
        headline = f"对比上次有明显提升（{len(improvements)} 项指标上涨）"
        status = "improved"
    elif down_weight > up_weight and regressions:
        headline = f"对比上次数值有所回调（{len(regressions)} 项指标下降）"
        status = "regressed"
    else:
        headline = "对比上次配装或属性有平调变动"
        status = "adjusted"

    return PanelDelta(
        status=status,
        headline=headline,
        improvements=tuple(improvements),
        regressions=tuple(regressions),
        neutral_changes=tuple(neutral_changes),
        prev_created_at=prev.created_at,
        record_count=record_count,
    )


class PanelHistoryStore:
    """用户角色面板历史库（独立 SQLite + WAL + 线程锁）。"""

    def __init__(self, path: str | Path, *, timeout: float = 5.0):
        self.path = Path(path).expanduser()
        if self.path.name.endswith(("p5x.sqlite3", "p5x_zh.sqlite3")):
            raise PanelHistoryError(
                f"面板历史库不能指向资料库（{self.path.name}），请使用 {PANEL_DB_FILENAME}"
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
            raise PanelHistoryError(f"面板历史库无法打开：{exc}") from exc

    def _row_to_snapshot(self, row: sqlite3.Row) -> PanelSnapshot:
        try:
            stats = json.loads(row["stats_json"] or "{}")
            if not isinstance(stats, dict):
                stats = {}
        except ValueError:
            stats = {}
        try:
            skills_raw = json.loads(row["skills_json"] or "[]")
            skills = tuple(int(x) for x in skills_raw) if isinstance(skills_raw, list) else ()
        except (ValueError, TypeError):
            skills = ()
        return PanelSnapshot(
            id=int(row["id"]),
            platform_id=str(row["platform_id"]),
            user_id=str(row["user_id"]),
            display_name=str(row["display_name"]),
            character_slug=str(row["character_slug"]),
            character_name=str(row["character_name"]),
            level=row["level"],
            awareness=row["awareness"],
            weapon_name=str(row["weapon_name"] or ""),
            weapon_level=row["weapon_level"],
            weapon_mod=row["weapon_mod"],
            revelation_set=str(row["revelation_set"] or ""),
            total_score=row["total_score"],
            grade=str(row["grade"] or ""),
            stats={str(k): str(v) for k, v in stats.items()},
            skill_levels=skills,
            created_at=str(row["created_at"]),
        )

    def _latest_locked(
        self, *, platform_id: str, user_id: str, character_slug: str
    ) -> tuple[PanelSnapshot | None, int]:
        row = self._conn.execute(
            f"""
            SELECT * FROM {TABLE_NAME}
            WHERE platform_id = ? AND user_id = ? AND character_slug = ?
            ORDER BY id DESC LIMIT 1
            """,
            (platform_id, user_id, character_slug),
        ).fetchone()
        cnt_row = self._conn.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM {TABLE_NAME}
            WHERE platform_id = ? AND user_id = ? AND character_slug = ?
            """,
            (platform_id, user_id, character_slug),
        ).fetchone()
        count = int(cnt_row["cnt"]) if cnt_row else 0
        return (self._row_to_snapshot(row) if row is not None else None), count

    def latest(
        self, *, platform_id: str, user_id: str, character_slug: str
    ) -> tuple[PanelSnapshot | None, int]:
        """查询该用户该角色的最新快照与累计历史条数。"""
        with self._lock:
            return self._latest_locked(
                platform_id=platform_id,
                user_id=user_id,
                character_slug=character_slug,
            )

    def _insert_locked(
        self,
        *,
        platform_id: str,
        user_id: str,
        display_name: str,
        parsed: panel_review.ParsedPanel,
        grade: str,
    ) -> None:
        now = _now_iso()
        self._conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                platform_id, user_id, display_name,
                character_slug, character_name,
                level, awareness,
                weapon_name, weapon_level, weapon_mod,
                revelation_set, total_score, grade,
                stats_json, skills_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform_id,
                user_id,
                display_name,
                parsed.character_slug,
                parsed.character_name,
                parsed.level,
                parsed.awareness,
                parsed.weapon_name,
                parsed.weapon_level,
                parsed.weapon_mod,
                parsed.revelation_set,
                parsed.total_score,
                grade,
                json.dumps(parsed.stat_map(), ensure_ascii=False),
                json.dumps(list(parsed.skill_levels)),
                now,
            ),
        )
        self._conn.commit()

    def record_and_compare(
        self,
        *,
        platform_id: str,
        user_id: str,
        display_name: str,
        parsed: panel_review.ParsedPanel,
        grade: str,
    ) -> PanelDelta:
        """先查上一条记录做成长对比；仅在首次或面板有实质变动时落库新快照。"""
        if not user_id or not parsed.character_slug:
            return PanelDelta(status="first", headline="已记录本次面板", record_count=1)

        with self._lock:
            prev, count = self._latest_locked(
                platform_id=platform_id,
                user_id=user_id,
                character_slug=parsed.character_slug,
            )
            if prev is None:
                self._insert_locked(
                    platform_id=platform_id,
                    user_id=user_id,
                    display_name=display_name,
                    parsed=parsed,
                    grade=grade,
                )
                return PanelDelta(
                    status="first",
                    headline="首次记录该角色面板，已存档以便后续追踪提升",
                    record_count=1,
                )

            delta = compare_panel(prev, parsed, grade, record_count=count + 1)
            if delta.status != "same":
                self._insert_locked(
                    platform_id=platform_id,
                    user_id=user_id,
                    display_name=display_name,
                    parsed=parsed,
                    grade=grade,
                )
            else:
                # 面板无变化时不重复插入垃圾行，保留原记录数
                delta = PanelDelta(
                    status="same",
                    headline=delta.headline,
                    prev_created_at=delta.prev_created_at,
                    record_count=count,
                )
            return delta

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) AS cnt FROM {TABLE_NAME}").fetchone()
        return int(row["cnt"]) if row else 0

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


__all__ = [
    "PANEL_DB_FILENAME",
    "TABLE_NAME",
    "PanelHistoryError",
    "PanelSnapshot",
    "PanelDelta",
    "PanelHistoryStore",
    "compare_panel",
]
