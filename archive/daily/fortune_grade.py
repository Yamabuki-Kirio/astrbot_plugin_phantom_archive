"""塔罗吉凶评级与综合建议：纯查表 + 纯规则，不调用任何模型。

五档：大吉 / 吉 / 平 / 凶 / 大凶。

分级依据 78 张牌的通行正逆位释义（牌义数据本身在 :mod:`tarot`）：

1. 正位：大阿卡纳按传统牌义给固定档；小阿卡纳按「花色 × 点数」给档 ——
   权杖看行动、圣杯看情感、宝剑看冲突、星币看物质，各自正负面不同；
2. 逆位：一律降一档（大吉→吉→平→凶→大凶，大凶不再上调），
   也就是「平的降成凶、凶的降成大凶」；
3. 少数逆位在牌义上是解脱或转机（恶魔挣脱束缚、宝剑十否极泰来、
   高塔避开灾难……），这类牌用 :data:`REVERSED_OVERRIDES` 单独给档，
   避免出现「牌义写觉醒、评级却是大凶」的自相矛盾。

综合建议由「牌名 + 正逆位 + 牌义 + 评级」拼成，文案写在
:data:`GRADE_ACTIONS` 与 :data:`POSITION_TAILS` 里，确定性、可测试。

这一层是纯数据，不 import tarot，避免 models ← tarot ← models 的循环。
"""

from __future__ import annotations

GRADE_GREAT = "大吉"
GRADE_GOOD = "吉"
GRADE_FLAT = "平"
GRADE_BAD = "凶"
GRADE_AWFUL = "大凶"

GRADES: tuple[str, ...] = (GRADE_GREAT, GRADE_GOOD, GRADE_FLAT, GRADE_BAD, GRADE_AWFUL)
DEFAULT_GRADE = GRADE_FLAT

# 逆位统一降一档；最差再无处可降
REVERSED_SHIFT: dict[str, str] = {
    GRADE_GREAT: GRADE_GOOD,
    GRADE_GOOD: GRADE_FLAT,
    GRADE_FLAT: GRADE_BAD,
    GRADE_BAD: GRADE_AWFUL,
    GRADE_AWFUL: GRADE_AWFUL,
}

# 每个档位的行动口径：(宜, 忌)
GRADE_GUIDES: dict[str, tuple[str, str]] = {
    GRADE_GREAT: ("主动出击，抓住眼前的机会", "犹豫拖延、错过窗口"),
    GRADE_GOOD: ("稳中求进，按计划推进", "贪多求快、临时改主意"),
    GRADE_FLAT: ("守成观察，先把手上的事做扎实", "盲目冒进、与人争执"),
    GRADE_BAD: ("低调收敛，先收尾旧账", "签约、争执与重大决定"),
    GRADE_AWFUL: ("按兵不动，只做非做不可的事", "开新项目、借钱与下重注"),
}

# 每个档位的一句话判断，供综合建议拼接
GRADE_ACTIONS: dict[str, str] = {
    GRADE_GREAT: "这是今天最好的一档：想做的事可以直接推进，重要的沟通尽量排在前面。",
    GRADE_GOOD: "整体是顺的：按既定节奏走就行，别临时换方向。",
    GRADE_FLAT: "没有明显加分也没有大坑：守成比冒进划算，先把手上没做完的事补齐。",
    GRADE_BAD: "阻力偏大：重要决定能拖就拖，先在旧账上收尾，别在情绪里做选择。",
    GRADE_AWFUL: "今天不适合推进新事：能不出手就不出手，先守住底线、控制风险。",
}

# 逆位额外补一句收束，正位不加
POSITION_TAILS: dict[bool, str] = {
    False: "",
    True: "逆位的能量更偏内耗，慢一点、往回收一点，不算退步。",
}

# 大阿卡纳正位档。键是中文牌名，与 tarot.MAJOR_ARCANA 的写法解耦。
MAJOR_GRADES: dict[str, str] = {
    "愚者": GRADE_GOOD,
    "魔术师": GRADE_GREAT,
    "女祭司": GRADE_GOOD,
    "皇后": GRADE_GREAT,
    "皇帝": GRADE_GOOD,
    "教皇": GRADE_GOOD,
    "恋人": GRADE_GREAT,
    "战车": GRADE_GREAT,
    "力量": GRADE_GOOD,
    "隐士": GRADE_FLAT,
    "命运之轮": GRADE_GREAT,
    "正义": GRADE_GOOD,
    "倒吊人": GRADE_FLAT,
    "死神": GRADE_BAD,
    "节制": GRADE_GOOD,
    "恶魔": GRADE_BAD,
    "高塔": GRADE_AWFUL,
    "星星": GRADE_GOOD,
    "月亮": GRADE_FLAT,
    "太阳": GRADE_GREAT,
    "审判": GRADE_GOOD,
    "世界": GRADE_GREAT,
}

MINOR_RANKS: tuple[str, ...] = (
    "王牌", "二", "三", "四", "五", "六", "七",
    "八", "九", "十", "侍从", "骑士", "王后", "国王",
)

# 四种花色各自 14 张的正位档
SUIT_GRADES: dict[str, tuple[str, ...]] = {
    # 权杖：行动与热情。开头猛、过程有摩擦、胜利收尾
    "权杖": (
        GRADE_GREAT, GRADE_FLAT, GRADE_GOOD, GRADE_GOOD, GRADE_BAD,
        GRADE_GREAT, GRADE_FLAT, GRADE_GOOD, GRADE_FLAT, GRADE_BAD,
        GRADE_GOOD, GRADE_GOOD, GRADE_GOOD, GRADE_GOOD,
    ),
    # 圣杯：情感与人际，正向牌最多
    "圣杯": (
        GRADE_GREAT, GRADE_GOOD, GRADE_GREAT, GRADE_FLAT, GRADE_BAD,
        GRADE_GOOD, GRADE_FLAT, GRADE_FLAT, GRADE_GREAT, GRADE_GREAT,
        GRADE_GOOD, GRADE_GOOD, GRADE_GOOD, GRADE_GOOD,
    ),
    # 宝剑：思虑与冲突，负面牌最集中
    "宝剑": (
        GRADE_GOOD, GRADE_FLAT, GRADE_BAD, GRADE_FLAT, GRADE_BAD,
        GRADE_FLAT, GRADE_FLAT, GRADE_BAD, GRADE_BAD, GRADE_AWFUL,
        GRADE_FLAT, GRADE_FLAT, GRADE_GOOD, GRADE_GOOD,
    ),
    # 星币：现实与积累，稳但少有爆点
    "星币": (
        GRADE_GOOD, GRADE_FLAT, GRADE_GOOD, GRADE_GOOD, GRADE_BAD,
        GRADE_GOOD, GRADE_GOOD, GRADE_GOOD, GRADE_GOOD, GRADE_GREAT,
        GRADE_GOOD, GRADE_GOOD, GRADE_GOOD, GRADE_GREAT,
    ),
}

SUITS: tuple[str, ...] = tuple(SUIT_GRADES)

# 逆位比「降一档」更好（或更坏）的牌：牌义写的是解脱 / 转机 / 明确恶化，
# 评级不能机械地按一档处理。键用中文牌名（大阿卡纳）或小阿卡纳全名。
REVERSED_OVERRIDES: dict[str, str] = {
    # 逆位反而是解脱、转机
    "恶魔": GRADE_GOOD,
    "高塔": GRADE_FLAT,
    "月亮": GRADE_GOOD,
    "权杖五": GRADE_FLAT,
    "权杖十": GRADE_GOOD,
    "圣杯四": GRADE_GOOD,
    "圣杯五": GRADE_FLAT,
    "圣杯七": GRADE_FLAT,
    "宝剑二": GRADE_GOOD,
    "宝剑三": GRADE_FLAT,
    "宝剑四": GRADE_GOOD,
    "宝剑五": GRADE_FLAT,
    "宝剑七": GRADE_FLAT,
    "宝剑八": GRADE_FLAT,
    "宝剑九": GRADE_FLAT,
    "宝剑十": GRADE_BAD,
    "星币五": GRADE_FLAT,
    # 逆位比降一档更糟：牌义是破裂、失控、失去
    "战车": GRADE_BAD,
    "命运之轮": GRADE_BAD,
    "恋人": GRADE_FLAT,
    "星币王牌": GRADE_BAD,
    "圣杯十": GRADE_BAD,
    "星币十": GRADE_BAD,
}


def major_name(card_name: str) -> str:
    """从「I 魔术师 The Magician」里取出中文牌名；小阿卡纳返回空串。

    按 token 逐个比对已知中文牌名，而不是固定取第二段 ——
    上游改序号、加后缀、去掉英文名都不会让评级掉成「平」。
    """
    for token in (card_name or "").split():
        if token in MAJOR_GRADES:
            return token
    return ""


def normalized_name(card_name: str) -> str:
    """统一成查表键：大阿卡纳取中文牌名，小阿卡纳本来就是全名。"""
    return major_name(card_name) or (card_name or "").strip()


def minor_key(card_name: str) -> tuple[str, int] | None:
    """把「权杖王牌」拆成 ``("权杖", 0)``；认不出返回 ``None``。"""
    name = card_name or ""
    for suit in SUITS:
        if not name.startswith(suit):
            continue
        rank = name[len(suit):]
        if rank in MINOR_RANKS:
            return suit, MINOR_RANKS.index(rank)
    return None


def base_grade(card_name: str) -> str:
    """正位档。认不出的牌按「平」处理，绝不抛异常。"""
    major = MAJOR_GRADES.get(major_name(card_name))
    if major:
        return major
    key = minor_key(card_name)
    if key is None:
        return DEFAULT_GRADE
    suit, index = key
    return SUIT_GRADES[suit][index]


def grade_of(card_name: str, is_reversed: bool = False) -> str:
    """最终档位：正位取基准档；逆位先查例外表，否则降一档。"""
    grade = base_grade(card_name)
    if not is_reversed:
        return grade
    override = REVERSED_OVERRIDES.get(normalized_name(card_name))
    if override:
        return override
    return REVERSED_SHIFT[grade]


def guide_of(grade: str) -> tuple[str, str]:
    """``(宜, 忌)``。未知档位按「平」给建议。"""
    return GRADE_GUIDES.get(grade, GRADE_GUIDES[DEFAULT_GRADE])


def build_advice(card_name: str, meaning: str, is_reversed: bool = False, *, grade: str = "") -> str:
    """综合建议：牌名 + 正逆位 + 牌义 + 评级口径，拼成两三句话。

    完全由查表决定，同一个输入永远同一段话；不调用模型。
    """
    grade = grade or grade_of(card_name, is_reversed)
    name = normalized_name(card_name)
    if not name:
        return "牌面信息不足，今天按平常节奏走就好。"

    position = "逆位" if is_reversed else "正位"
    meaning = (meaning or "").strip()
    lead = (
        f"「{name}」{position}，牌面说的是「{meaning}」。"
        if meaning
        else f"「{name}」{position}，牌面释义暂缺。"
    )
    action = GRADE_ACTIONS.get(grade, GRADE_ACTIONS[DEFAULT_GRADE])
    tail = POSITION_TAILS[bool(is_reversed)]
    return f"{lead}{action}{tail}"


def describe(card_name: str, is_reversed: bool = False, meaning: str = "") -> dict:
    """一次算齐展示层需要的字段，避免调用方各算一遍。"""
    grade = grade_of(card_name, is_reversed)
    suitable, avoid = guide_of(grade)
    return {
        "grade": grade,
        "suitable": suitable,
        "avoid": avoid,
        "advice": build_advice(card_name, meaning, is_reversed, grade=grade),
    }


__all__ = [
    "GRADES",
    "GRADE_GREAT",
    "GRADE_GOOD",
    "GRADE_FLAT",
    "GRADE_BAD",
    "GRADE_AWFUL",
    "DEFAULT_GRADE",
    "REVERSED_SHIFT",
    "GRADE_GUIDES",
    "GRADE_ACTIONS",
    "POSITION_TAILS",
    "MAJOR_GRADES",
    "MINOR_RANKS",
    "SUIT_GRADES",
    "REVERSED_OVERRIDES",
    "major_name",
    "normalized_name",
    "minor_key",
    "base_grade",
    "grade_of",
    "guide_of",
    "build_advice",
    "describe",
]
