"""干支基础工具：天干地支与日干支推算（纯 Python，无依赖）。

日干支以 1949-10-01 为甲子日锚点。这是民间通行的一种换日约定，
与「夜子时换日」等流派算法不同；卡片上只把干支当日历装饰用，
不参与任何运势推断，所以不需要处理子时归属问题。
"""

from __future__ import annotations

from datetime import date

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

ANCHOR = date(1949, 10, 1)


def stem_branch(index: int) -> str:
    """干支序号（0 = 甲子）→ 干支字符串。"""
    return STEMS[index % 10] + BRANCHES[index % 12]


def day_ganzhi_index(day: date) -> int:
    """某天的日干支序号，范围 0–59。"""
    return (day - ANCHOR).days % 60


def day_ganzhi(day: date) -> str:
    """某天的日干支，例如 ``甲子``。"""
    return stem_branch(day_ganzhi_index(day))


__all__ = ["STEMS", "BRANCHES", "ANCHOR", "stem_branch", "day_ganzhi_index", "day_ganzhi"]
