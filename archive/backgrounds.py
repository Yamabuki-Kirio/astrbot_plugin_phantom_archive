"""三套卡片背景；一次请求选一套，多页保持一致。"""
from itertools import cycle
from random import choice

BACKGROUND_FILES = {
    "phantom": "phantom-card.png",
    "reload_blue": "reload-blue.png",
    "phantom_red": "phantom-red.png",
}
BACKGROUND_SIZES = {
    "phantom": (1482, 1062),
    "reload_blue": (1672, 941),
    "phantom_red": (1672, 941),
}


def normalize_background(value: str) -> str:
    return value if value in BACKGROUND_FILES else "phantom"


class BackgroundRotation:
    def __init__(self):
        self._cycle = cycle(BACKGROUND_FILES)

    def select(self, *, rotate: bool = True, fixed: str = "phantom", mode: str = "sequential") -> str:
        if not rotate or mode == "fixed":
            return normalize_background(fixed)
        if mode == "random":
            return choice(tuple(BACKGROUND_FILES))
        return next(self._cycle)


def resolve_background_mode(mode=None, *, legacy_rotate=True, legacy_mode="sequential"):
    """显式新配置优先；未设置时兼容旧版开关与轮换方式。"""
    if mode in ("sequential", "random", "fixed"):
        return mode
    if not legacy_rotate:
        return "fixed"
    return "random" if legacy_mode == "random" else "sequential"
