"""每日运势引擎：把「日期 + 用户 + 角色池」变成一份确定的 :class:`DailyFortune`。

随机数隔离（规格里的硬性要求）：

    fortune_seed   = SHA256("fortune|日期|平台|用户ID")
    character_seed = SHA256("character|日期|平台|用户ID")

两把种子分开，是为了让「塔罗」和「今日角色」互不影响：
以后想单独调整角色池（比如只从 5★ 里抽），不会连带把塔罗全改一遍。

算法里**没有** MD5，也没有 ``random.random()`` 之外的隐式随机源：
所有随机都来自 ``random.Random(种子)``，同一个种子在任何进程、任何机器上
都给出同一串数。
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import date, datetime
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import arcana_affinity, character_draw, ganzhi, tarot
from .models import DailyCharacter, DailyFortune, TarotDraw

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Shanghai"

FORTUNE_SALT = "fortune"
CHARACTER_SALT = "character"
SEED_SEPARATOR = "|"


class FortuneUnavailableError(Exception):
    """拿不到可用结果时抛这个，由上层翻译成固定文案。"""


# ---------------------------------------------------------------- 时区与日期


def resolve_timezone(name: str | None) -> tuple[ZoneInfo, str]:
    """解析时区名。非法值回退 ``Asia/Shanghai`` 并警告，绝不因此报错。"""
    candidate = (name or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(candidate), candidate
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("daily_fortune_timezone 配置非法（%r），回退 %s", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE), DEFAULT_TIMEZONE


def today_in(tz: ZoneInfo, now: datetime | None = None) -> date:
    """配置时区下的「今天」。now 为 None 时取系统当前时间。"""
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.astimezone(tz).date()


def date_key_of(day: date) -> str:
    return day.isoformat()


def parse_date_key(date_key: str) -> date:
    try:
        return date.fromisoformat(date_key)
    except (TypeError, ValueError) as exc:
        raise FortuneUnavailableError(f"日期键非法：{date_key!r}") from exc


# ---------------------------------------------------------------- 种子


def make_seed(salt: str, date_key: str, platform_id: str, user_id: str) -> bytes:
    """``SHA256("salt|日期|平台|用户ID")``。用 UTF-8 编码，中文昵称不参与。"""
    raw = SEED_SEPARATOR.join((salt, date_key, platform_id or "", user_id or ""))
    return hashlib.sha256(raw.encode("utf-8")).digest()


def fortune_seed(date_key: str, platform_id: str, user_id: str) -> bytes:
    return make_seed(FORTUNE_SALT, date_key, platform_id, user_id)


def character_seed(date_key: str, platform_id: str, user_id: str) -> bytes:
    return make_seed(CHARACTER_SALT, date_key, platform_id, user_id)


def seed_int(seed: bytes) -> int:
    """``random.Random`` 的种子：取前 8 字节，足够且与平台无关。"""
    return int.from_bytes(seed[:8], "big")


# ---------------------------------------------------------------- 生成


def draw_fortune(
    *,
    date_key: str,
    platform_id: str,
    user_id: str,
    characters: Sequence[DailyCharacter],
) -> tuple[DailyCharacter, TarotDraw]:
    """纯抽取，不碰数据库。

    先抽塔罗；若大阿卡纳有已进入 P5X 的 P3/P5 对应角色，则从对应角色中
    确定性选一名，否则从完整角色池选取。角色仍使用独立种子，因此同日同人固定。
    """
    rng = random.Random(seed_int(fortune_seed(date_key, platform_id, user_id)))
    card = tarot.draw(rng)
    preferred = arcana_affinity.preferred_candidates(card, characters)
    chosen = character_draw.draw(
        preferred or characters,
        character_seed(date_key, platform_id, user_id),
    )
    return chosen, card


def generate(
    *,
    date_key: str,
    timezone: str,
    platform_id: str,
    user_id: str,
    display_name: str = "",
    characters: Iterable[DailyCharacter],
) -> DailyFortune:
    """生成一份完整的每日运势。候选池为空时抛 :class:`FortuneUnavailableError`。"""
    day = parse_date_key(date_key)
    pool = character_draw.candidates(characters)
    if not pool:
        raise FortuneUnavailableError("角色库为空，无法抽取今日角色")

    chosen, card = draw_fortune(
        date_key=date_key,
        platform_id=platform_id,
        user_id=user_id,
        characters=pool,
    )
    return DailyFortune(
        date_key=date_key,
        timezone=timezone,
        platform_id=platform_id,
        user_id=user_id,
        display_name=display_name,
        character=chosen,
        tarot=card,
        ganzhi=ganzhi.day_ganzhi(day),
        stored=False,
    )


__all__ = [
    "DEFAULT_TIMEZONE",
    "FORTUNE_SALT",
    "CHARACTER_SALT",
    "FortuneUnavailableError",
    "character_seed",
    "date_key_of",
    "draw_fortune",
    "fortune_seed",
    "generate",
    "make_seed",
    "parse_date_key",
    "resolve_timezone",
    "seed_int",
    "today_in",
]
