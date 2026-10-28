"""每日运势：抽角色 + 塔罗，输出一张卡片或一段文字。

模块边界：

    models        纯数据结构（含序列化）
    engine        种子、时区、抽取编排
    tarot         塔罗数据与单张抽取
    ganzhi        干支
    character_draw 今日角色（确定性、与行序无关）
    repository    独立每日库的读写
    presenter     一份结果 → 卡片 / 文字

第三方来源见仓库根目录 ``THIRD_PARTY_NOTICES.md``。
"""

from __future__ import annotations

from . import (
    character_draw,
    engine,
    ganzhi,
    models,
    presenter,
    repository,
    tarot,
)

__all__ = [
    "character_draw",
    "engine",
    "ganzhi",
    "models",
    "presenter",
    "repository",
    "tarot",
]
