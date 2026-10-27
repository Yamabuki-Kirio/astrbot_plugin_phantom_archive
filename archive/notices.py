"""面向用户的提示文案 —— 唯一出处。

报告、查询层、渲染器、测试都必须引用这里的同一份文本。
分散各处手写会导致措辞漂移，而这类文案一旦漂移，
就等于把社区资料说成了官方资料。

与 p5x-data-crawler/zh/notices.mjs 保持一致；两者不一致时
`load_from_file()` 会在启动日志里给出警告。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 通用免责声明，按行拆开便于排版时控制换行
DISCLAIMER_LINES: tuple[str, ...] = (
    "资料来源：Lufel 社区资料",
    "部分名称和攻略经过机器翻译，尚未完成国服逐项核对。",
    "检测到部分区服数值存在差异，请以当前国服游戏内数据为准。",
)

# 多行合并版本，直接塞进消息尾部
DISCLAIMER = "\n".join(DISCLAIMER_LINES)

# 单行紧凑版本，用于字数紧张或帮助文本
DISCLAIMER_ONELINE = (
    "资料来源：Lufel 社区资料；部分内容为机器翻译、未完成国服逐项核对；数值差异请以国服游戏内为准。"
)

# 命中 numeric_conflict = 1 的条目时追加的一行警告
NUMERIC_CONFLICT_WARNING = "⚠ 该条目的不同语言来源存在数值差异，当前结果仅供参考。"

# 给不想看内部术语的用户看的自然语言版本（§12）
NUMERIC_CONFLICT_PLAIN = "检测到该资料不同语言版本的数值存在差异。"

# 来源状态 → 展示用标签。绝不把 community 渲染成「官方」
SOURCE_STATUS_LABEL: dict[str, str] = {
    "official": "国服官方",
    "community": "Lufel 社区资料",
}

# 翻译方式 → 展示用标签
TRANSLATION_STATUS_LABEL: dict[str, str] = {
    "source_original": "来源原文",
    "machine_translated": "机器翻译",
    "needs_review": "待人工审核",
}

# 风险从高到低，用于一条回复里多种状态并存时取最差
TRANSLATION_STATUS_SEVERITY: dict[str, int] = {
    "source_original": 0,
    "machine_translated": 1,
    "needs_review": 2,
}

# 记录为 needs_review 时，标题行后面挂的角标
NEEDS_REVIEW_BADGE = "[待人工审核]"

# 缺失字段的统一降级文案，禁止自行生成名称（§7）
MISSING_WEAPON_NAME_TEMPLATE = "未收录名称（{key}）"
MISSING_SKILL_NAME_TEMPLATE = "未收录名称（{key}）"
MISSING_WEAPON_SKILL_NAME = "技能名称：暂未收录"

# 明确禁止出现在对外文案里的说法（§10），测试会扫一遍
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "官方译文",
    "官方数据库",
    "国服完整数据",
    "数据与国服完全一致",
)

# 帮助文本末尾的固定说明（§7）
HELP_SOURCE_NOTE = "当前资料为社区整理，非官方数据库。"


def source_label(status: str | None) -> str:
    """来源状态 → 展示标签，未知状态原样返回而不是猜一个。"""
    if not status:
        return "未知来源"
    return SOURCE_STATUS_LABEL.get(status, status)


def translation_label(status: str | None) -> str:
    """翻译方式 → 展示标签。"""
    if not status:
        return "未知"
    return TRANSLATION_STATUS_LABEL.get(status, status)


def worst_translation_status(statuses) -> str:
    """一条回复里可能混着多种状态，取风险最高的那个（§11）。"""
    worst = None
    worst_score = -1
    for status in statuses:
        if not status:
            continue
        score = TRANSLATION_STATUS_SEVERITY.get(status, 2)
        if score > worst_score:
            worst, worst_score = status, score
    return worst or "source_original"


def load_from_file(path: str | Path) -> dict:
    """读取 p5x-data-crawler 导出的 display_notices.json。

    仅用于一致性自检：模块常量仍是唯一真值来源，
    文件内容不一致时只记日志，不在运行时改用文件内容，
    否则「唯一出处」就名存实亡了。
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取提示文案文件失败：%s（%s）", path, exc)
        return {}

    if data.get("disclaimer") and data["disclaimer"] != DISCLAIMER:
        logger.warning("display_notices.json 的免责声明与 notices.py 不一致，请同步重新生成数据库")
    if data.get("numeric_conflict_warning") and data["numeric_conflict_warning"] != NUMERIC_CONFLICT_WARNING:
        logger.warning("display_notices.json 的数值冲突提示与 notices.py 不一致")
    return data
