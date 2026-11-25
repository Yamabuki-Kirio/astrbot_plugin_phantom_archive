"""P5X 角色面板图纯本地识别（RapidOCR）与一图流对标点评引擎。

不依赖任何 LLM 视觉模型：
1. 先按图片尺寸/长宽比快速预筛（<1ms），排除表情包、竖屏图与小图；
2. 使用 RapidOCR（ONNX Runtime + PP-OCRv4）提取文字与归一化坐标；
3. 校验至少命中 3 个 P5X 面板专属锚点 + 已知 P5X 角色名，避免误触发；
4. 按坐标解析角色名、等级、属性、技能等级、启示卡套装、武器名与综合评分；
5. 对标 ``data/yituliou/characters.json`` 生成结构化练度点评。
"""

from __future__ import annotations

import difflib
import io
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import yituliou

logger = logging.getLogger(__name__)

# 预筛阈值：P5X 角色分享面板均为横屏（常见 16:9 ~ 21:9）
MIN_WIDTH = 560
MIN_HEIGHT = 260
MIN_ASPECT = 1.35
MAX_ASPECT = 2.85
MAX_IMAGE_BYTES = 12 * 1024 * 1024

# P5X 角色面板图专属锚点词（命中 >= 3 个才判定为角色面板）
PANEL_ANCHORS: tuple[str, ...] = (
    "属性",
    "生命值",
    "攻击力",
    "防御力",
    "技能",
    "核心属性目标",
    "综合评分",
    "评级",
    "心象点数",
    "点击任意空白处继续",
    "解明之力",
    "详情",
)
MIN_ANCHOR_HITS = 3

# 可解析的面板属性名（按长度降序，便于前缀匹配）
STAT_LABELS: tuple[str, ...] = (
    "精力回复",
    "伤害加成",
    "暴击效果",
    "效果命中",
    "效果抵抗",
    "治疗效果",
    "护盾效果",
    "生命值",
    "攻击力",
    "防御力",
    "暴击率",
    "穿透",
    "速度",
)

STAT_ALIASES: dict[str, str] = {
    "攻击": "攻击力",
    "攻击力": "攻击力",
    "生命": "生命值",
    "生命值": "生命值",
    "防御": "防御力",
    "防御力": "防御力",
    "暴击": "暴击率",
    "暴击率": "暴击率",
    "爆击": "暴击率",
    "爆击率": "暴击率",
    "暴伤": "暴击效果",
    "爆伤": "暴击效果",
    "暴击效果": "暴击效果",
    "伤加": "伤害加成",
    "伤害加成": "伤害加成",
    "穿透": "穿透",
    "速度": "速度",
    "效果命中": "效果命中",
    "效果抵抗": "效果抵抗",
    "精力回复": "精力回复",
    "治疗效果": "治疗效果",
}

_OCR_LOCK = threading.Lock()
_OCR_INSTANCE: Any = None
_OCR_UNAVAILABLE: bool = False


@dataclass(frozen=True)
class OcrBox:
    text: str
    score: float
    nx0: float
    ny0: float
    nx1: float
    ny1: float

    @property
    def ncx(self) -> float:
        return (self.nx0 + self.nx1) / 2.0

    @property
    def ncy(self) -> float:
        return (self.ny0 + self.ny1) / 2.0


@dataclass(frozen=True)
class ParsedPanel:
    character_name: str
    character_slug: str
    level: int | None
    stats: tuple[tuple[str, str], ...]
    skill_levels: tuple[int, ...]
    revelation_set: str
    weapon_name: str
    weapon_level: int | None
    total_score: int | None
    anchor_hits: int

    def stat_map(self) -> dict[str, str]:
        return dict(self.stats)


@dataclass(frozen=True)
class ReviewItem:
    category: str
    status: str  # pass | warn | info
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class PanelReview:
    parsed: ParsedPanel
    entry: yituliou.YituliouEntry | None
    grade: str  # S | A | B | C
    headline: str
    items: tuple[ReviewItem, ...]

    def as_template_dict(self) -> dict[str, Any]:
        entry_dict = self.entry.as_template_dict() if self.entry is not None else None
        return {
            "character_name": self.parsed.character_name,
            "character_slug": self.parsed.character_slug,
            "level": self.parsed.level,
            "stats": [{"label": k, "value": v} for k, v in self.parsed.stats],
            "skill_levels": list(self.parsed.skill_levels),
            "revelation_set": self.parsed.revelation_set,
            "weapon_name": self.parsed.weapon_name,
            "weapon_level": self.parsed.weapon_level,
            "total_score": self.parsed.total_score,
            "grade": self.grade,
            "headline": self.headline,
            "items": [
                {
                    "category": it.category,
                    "status": it.status,
                    "summary": it.summary,
                    "detail": it.detail,
                }
                for it in self.items
            ],
            "entry": entry_dict,
        }


# ------------------------------------------------------------------ OCR 引擎


def ocr_available() -> bool:
    """检查本地 RapidOCR 依赖是否可用。"""
    global _OCR_UNAVAILABLE
    if _OCR_UNAVAILABLE:
        return False
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _get_ocr():
    global _OCR_INSTANCE, _OCR_UNAVAILABLE
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE
    if _OCR_UNAVAILABLE:
        return None
    with _OCR_LOCK:
        if _OCR_INSTANCE is not None:
            return _OCR_INSTANCE
        try:
            from rapidocr_onnxruntime import RapidOCR

            _OCR_INSTANCE = RapidOCR()
            return _OCR_INSTANCE
        except Exception as exc:  # noqa: BLE001
            _OCR_UNAVAILABLE = True
            logger.warning("本地 OCR 引擎初始化失败（%s），主动识图已自动跳过", type(exc).__name__)
            return None


def passes_size_prefilter(image_bytes: bytes) -> tuple[bool, tuple[int, int]]:
    """毫秒级尺寸预筛：仅允许横向中大分辨率静态图片进入 OCR。"""
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        return False, (0, 0)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            if getattr(img, "is_animated", False):
                return False, img.size
            w, h = img.size
    except Exception:  # noqa: BLE001
        return False, (0, 0)

    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return False, (w, h)
    aspect = w / h
    if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
        return False, (w, h)
    return True, (w, h)


def run_ocr(image_bytes: bytes) -> tuple[list[OcrBox], tuple[int, int]] | None:
    """对图片字节执行本地 RapidOCR，返回归一化坐标文本框列表。"""
    ok, size = passes_size_prefilter(image_bytes)
    if not ok:
        return None
    ocr = _get_ocr()
    if ocr is None:
        return None

    w, h = size
    with _OCR_LOCK:
        result, _ = ocr(image_bytes)
    if not result:
        return [], size

    boxes: list[OcrBox] = []
    for raw_box, text, score in result:
        clean = str(text or "").strip()
        if not clean:
            continue
        xs = [float(pt[0]) for pt in raw_box]
        ys = [float(pt[1]) for pt in raw_box]
        boxes.append(
            OcrBox(
                text=clean,
                score=float(score),
                nx0=min(xs) / w,
                ny0=min(ys) / h,
                nx1=max(xs) / w,
                ny1=max(ys) / h,
            )
        )
    return boxes, size


# ------------------------------------------------------------------ 面板解析


def _clean_ocr_label(text: str) -> str:
    return re.sub(r"^[\+\-·•・*oO0Q\s]+", "", text).strip(" ：:[]【】()（）口")


def _parse_numeric_value(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def _is_stat_value(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?%?", text.strip().replace(",", "")))


def _count_anchors(boxes: Sequence[OcrBox]) -> int:
    joined = " ".join(b.text for b in boxes)
    hits = 0
    for anchor in PANEL_ANCHORS:
        if anchor in joined:
            hits += 1
    return hits


def _match_character(
    boxes: Sequence[OcrBox],
    repo: yituliou.YituliouRepository,
    extra_characters: Sequence[tuple[str, str]] = (),
) -> tuple[str, str] | None:
    """在左上角标题区识别 P5X 角色名（支持 1 字 OCR 误识容错）。

    ``extra_characters`` 为 ``[(slug, display_name), ...]``，用于覆盖尚未进一图流的角色。
    """
    candidates_map: dict[str, tuple[str, str]] = {}
    for slug, name in extra_characters:
        if name:
            candidates_map[yituliou.normalize_key(name)] = (name, slug)
    for entry in repo.entries:
        candidates_map[yituliou.normalize_key(entry.name)] = (entry.name, entry.slug)

    # 左上标题区域优先
    header_boxes = [
        b
        for b in boxes
        if b.nx0 < 0.42 and b.ny0 < 0.28 and not re.match(r"^lv\.?\d+", b.text, re.I)
    ]
    if not header_boxes:
        return None

    # 1. 精确命中
    for box in header_boxes:
        raw = re.sub(r"lv\.?\s*\d+", "", box.text, flags=re.I).strip()
        key = yituliou.normalize_key(raw)
        if not key:
            continue
        if key in candidates_map:
            return candidates_map[key]

    # 2. 模糊匹配（应对如「佐爸双叶」→「佐仓双叶」的单字形近误识）
    best: tuple[float, str, str] | None = None
    for box in header_boxes:
        raw = re.sub(r"lv\.?\s*\d+", "", box.text, flags=re.I).strip()
        key = yituliou.normalize_key(raw)
        if len(key) < 2:
            continue
        for cand_key, (cand_name, cand_slug) in candidates_map.items():
            if len(cand_key) < 2:
                continue
            ratio = difflib.SequenceMatcher(None, key, cand_key).ratio()
            # 长度相同且仅差 1 字时给予轻微加成
            if len(key) == len(cand_key) and sum(a == b for a, b in zip(key, cand_key)) == len(key) - 1:
                ratio = max(ratio, 0.78)
            if ratio >= 0.72 and (best is None or ratio > best[0]):
                best = (ratio, cand_name, cand_slug)

    if best is not None:
        return best[1], best[2]
    return None


def _parse_stats(boxes: Sequence[OcrBox]) -> tuple[tuple[str, str], ...]:
    """提取左侧属性框（nx: 0.08~0.50, ny: 0.25~0.55）中的「属性名 -> 数值」。"""
    stat_region = [b for b in boxes if 0.08 <= b.nx0 <= 0.50 and 0.25 <= b.ny0 <= 0.55]
    found: dict[str, str] = {}

    for box in stat_region:
        cleaned = _clean_ocr_label(box.text)
        matched_label = None
        for label in STAT_LABELS:
            if label in cleaned:
                matched_label = label
                break
        if matched_label is None:
            continue

        # 情况 A：标签与数值在同一个 OCR 框里（例如「攻击力 7149」）
        tail = cleaned.split(matched_label, 1)[1].strip()
        m_inline = re.search(r"(\d+(?:\.\d+)?%?)", tail)
        if m_inline:
            found[matched_label] = m_inline.group(1)
            continue

        # 情况 B：数值在右侧同行独立框里（优先选纵坐标最贴近的，再选最近右侧）
        same_row_vals = [
            other
            for other in stat_region
            if other is not box
            and other.nx0 > box.nx0
            and (other.nx0 - box.nx0) < 0.20
            and abs(other.ncy - box.ncy) < 0.028
            and _is_stat_value(other.text)
        ]
        if same_row_vals:
            same_row_vals.sort(key=lambda o: (abs(o.ncy - box.ncy), o.nx0))
            found[matched_label] = same_row_vals[0].text.strip()

    ordered = [(label, found[label]) for label in STAT_LABELS if label in found]
    return tuple(ordered)


def _parse_skill_levels(boxes: Sequence[OcrBox]) -> tuple[int, ...]:
    """提取技能框（nx: 0.10~0.35, ny: 0.52~0.75）的 4 个技能等级，按 上左/上右/下左/下右 排序。"""
    candidates: list[tuple[float, float, int]] = []
    for b in boxes:
        if not (0.10 <= b.nx0 <= 0.35 and 0.52 <= b.ny0 <= 0.75):
            continue
        m = re.search(r"lv\.?\s*(\d{1,2})", b.text, re.I)
        if m:
            candidates.append((b.ncy, b.ncx, int(m.group(1))))
    if not candidates:
        return ()
    # 按行聚类（上下两行）再按列排序
    candidates.sort(key=lambda item: item[0])
    if len(candidates) >= 2:
        mid_y = (candidates[0][0] + candidates[-1][0]) / 2.0
        top_row = sorted([c for c in candidates if c[0] <= mid_y], key=lambda item: item[1])
        bot_row = sorted([c for c in candidates if c[0] > mid_y], key=lambda item: item[1])
        ordered = top_row + bot_row
    else:
        ordered = candidates
    return tuple(lv for _, _, lv in ordered[:4])


def _parse_revelation(boxes: Sequence[OcrBox]) -> str:
    """提取左下启示卡套装名，如「【决心】&【职责】」。"""
    for b in boxes:
        if b.nx0 > 0.40 or b.ny0 < 0.68:
            continue
        m = re.search(r"[【\[]([^】\]]+)[】\]]\s*[&＆+＋]\s*[【\[]([^】\]]+)[】\]]", b.text)
        if m:
            return f"【{m.group(1).strip()}】&【{m.group(2).strip()}】"
    return ""


def _parse_weapon(boxes: Sequence[OcrBox]) -> tuple[str, int | None]:
    """提取中下武器框（nx: 0.27~0.48, ny: 0.68~0.92）的武器等级与武器名。"""
    weapon_level: int | None = None
    weapon_name = ""
    region = [b for b in boxes if 0.27 <= b.nx0 <= 0.48 and 0.68 <= b.ny0 <= 0.92]
    for b in sorted(region, key=lambda item: item.ny0):
        m_lv = re.search(r"^lv\.?\s*(\d{1,3})$", b.text.strip(), re.I)
        if m_lv:
            weapon_level = int(m_lv.group(1))
            continue
        if any(skip in b.text for skip in ("心象", "点击", "【", "&", "技能")):
            continue
        if len(b.text.strip()) >= 2:
            weapon_name = b.text.strip()
    return weapon_name, weapon_level


def parse_panel_ocr(
    boxes: Sequence[OcrBox],
    repo: yituliou.YituliouRepository,
    extra_characters: Sequence[tuple[str, str]] = (),
) -> ParsedPanel | None:
    """将 OCR 文本框解析为结构化角色面板；非 P5X 面板返回 None。"""
    anchor_hits = _count_anchors(boxes)
    if anchor_hits < MIN_ANCHOR_HITS:
        return None

    matched = _match_character(boxes, repo, extra_characters=extra_characters)
    if matched is None:
        return None
    char_name, char_slug = matched

    # 角色等级（左上角）
    char_level: int | None = None
    for b in boxes:
        if b.nx0 < 0.42 and b.ny0 < 0.28:
            m = re.search(r"lv\.?\s*(\d{1,3})", b.text, re.I)
            if m:
                char_level = int(m.group(1))
                break

    # 综合评分
    total_score: int | None = None
    for b in boxes:
        m = re.search(r"综合评分[:：]?\s*(\d{3,6})", b.text)
        if m:
            total_score = int(m.group(1))
            break

    stats = _parse_stats(boxes)
    skill_levels = _parse_skill_levels(boxes)
    revelation_set = _parse_revelation(boxes)
    weapon_name, weapon_level = _parse_weapon(boxes)

    # 至少解析出 1 项核心练度字段（属性 / 技能 / 启示卡 / 武器）才算有效面板
    if not (stats or skill_levels or revelation_set or weapon_name):
        return None

    return ParsedPanel(
        character_name=char_name,
        character_slug=char_slug,
        level=char_level,
        stats=stats,
        skill_levels=skill_levels,
        revelation_set=revelation_set,
        weapon_name=weapon_name,
        weapon_level=weapon_level,
        total_score=total_score,
        anchor_hits=anchor_hits,
    )


# ------------------------------------------------------------------ 一图流对标点评


def _normalize_rev_set(text: str) -> str:
    return re.sub(r"[\s【】\[\]《》&＆+＋·•・]", "", text or "")


def _extract_stat_thresholds(sub_stats: Sequence[str]) -> list[tuple[str, float, bool]]:
    """从一图流副词条中提取数值门槛，如「攻击力＞5060」→ [('攻击力', 5060.0, False)]。"""
    thresholds: list[tuple[str, float, bool]] = []
    pattern = re.compile(r"([\u4e00-\u9fa5]{2,4})\s*[>＞≥>=]+\s*(\d+(?:\.\d+)?)(%?)")
    for line in sub_stats:
        for m in pattern.finditer(line):
            raw_label, num_str, pct = m.groups()
            canonical = STAT_ALIASES.get(raw_label)
            if canonical:
                thresholds.append((canonical, float(num_str), bool(pct)))
    return thresholds


def _evaluate_skills(
    skill_levels: Sequence[int], priority: str
) -> ReviewItem:
    labels = ("①", "②", "③", "战斗技巧")
    lv_str = " / ".join(f"{labels[i]} Lv.{lv}" for i, lv in enumerate(skill_levels[:4]))
    if not priority:
        return ReviewItem("技能加点", "info", f"当前技能：{lv_str}")

    # 解析一图流加点建议中的核心技能编号（①/②/③）
    core_indices: list[int] = []
    for sym, idx in (("①", 0), ("②", 1), ("③", 2)):
        # 若写明「②技能可不点」或「②技能影响较低」，不把该技能算入强制核心
        if re.search(rf"{sym}技能[^，。,；;）)]*(?:可不点|影响较低|不点)", priority):
            continue
        if sym in priority:
            core_indices.append(idx)
    if not core_indices:
        core_indices = [0, 2]

    low_core = [
        f"{labels[i]}(Lv.{skill_levels[i]})"
        for i in core_indices
        if i < len(skill_levels) and skill_levels[i] < 10
    ]
    if not low_core:
        extra = ""
        if len(skill_levels) >= 4 and skill_levels[3] < 8 and "战斗技巧" in priority and "战斗技巧可不点" not in priority:
            extra = f"；战斗技巧当前 Lv.{skill_levels[3]}，资源充裕可继续补至 Lv.8+"
        return ReviewItem(
            "技能加点",
            "pass",
            f"核心技能已达标（{lv_str}）{extra}",
            detail=f"一图流推荐：{priority}",
        )
    return ReviewItem(
        "技能加点",
        "warn",
        f"核心技能偏低：{', '.join(low_core)}，建议优先拉至 Lv.10",
        detail=f"当前：{lv_str} ｜ 推荐：{priority}",
    )


def evaluate_panel(
    parsed: ParsedPanel,
    repo: yituliou.YituliouRepository,
) -> PanelReview:
    """将解析出的面板与一图流数据逐项对标，输出综合练度评级与点评。"""
    entries = repo.find_all_by_slug(parsed.character_slug)
    entry = entries[0] if len(entries) == 1 else None
    # 若双子有 3 种模式，尝试按启示卡套装反推当前模式
    if entry is None and len(entries) > 1 and parsed.revelation_set:
        norm_rev = _normalize_rev_set(parsed.revelation_set)
        for cand in entries:
            all_sets = (*cand.revelation.primary, *cand.revelation.alternative)
            if any(_normalize_rev_set(s) == norm_rev for s in all_sets):
                entry = cand
                break
        if entry is None:
            entry = entries[0]

    items: list[ReviewItem] = []
    pass_count = 0
    warn_count = 0

    if entry is None:
        items.append(
            ReviewItem(
                "一图流数据",
                "info",
                f"已识别角色「{parsed.character_name}」，但当前一图流合集尚未收录该角色，仅展示面板解析结果。",
            )
        )
        return PanelReview(
            parsed=parsed,
            entry=None,
            grade="—",
            headline=f"已识别 {parsed.character_name} 面板（暂未收录一图流对标数据）",
            items=tuple(items),
        )

    # 1. 启示卡点评
    rev = entry.revelation
    if parsed.revelation_set:
        norm_actual = _normalize_rev_set(parsed.revelation_set)
        prim_norms = [_normalize_rev_set(s) for s in rev.primary]
        alt_norms = [_normalize_rev_set(s) for s in rev.alternative]
        if any(norm_actual == p for p in prim_norms if p):
            pass_count += 1
            items.append(
                ReviewItem(
                    "启示卡套装",
                    "pass",
                    f"已装备毕业首选套装 {parsed.revelation_set}",
                    detail=f"主词条参考：{' / '.join(f'{k}:{v}' for k, v in rev.main_stats)}" if rev.main_stats else "",
                )
            )
        elif any(norm_actual == a for a in alt_norms if a):
            pass_count += 1
            items.append(
                ReviewItem(
                    "启示卡套装",
                    "info",
                    f"当前为备选套装 {parsed.revelation_set}（可用）",
                    detail=f"毕业首选推荐：{' / '.join(rev.primary)}" if rev.primary else "",
                )
            )
        else:
            warn_count += 1
            rec_parts = []
            if rev.primary:
                rec_parts.append("首选 " + " / ".join(rev.primary))
            if rev.alternative:
                rec_parts.append("备选 " + " / ".join(rev.alternative))
            items.append(
                ReviewItem(
                    "启示卡套装",
                    "warn",
                    f"当前 {parsed.revelation_set} 非一图流推荐套装",
                    detail="建议更换：" + "；".join(rec_parts) if rec_parts else "",
                )
            )
    elif rev.primary:
        items.append(
            ReviewItem(
                "启示卡套装",
                "info",
                f"未识别到启示卡，一图流首选推荐：{' / '.join(rev.primary)}",
            )
        )

    # 2. 武器点评
    if parsed.weapon_name:
        lv_tag = f"（Lv.{parsed.weapon_level}）" if parsed.weapon_level else ""
        if entry.weapon_name:
            actual_w = yituliou.normalize_key(parsed.weapon_name)
            target_w = yituliou.normalize_key(entry.weapon_name)
            sim = difflib.SequenceMatcher(None, actual_w, target_w).ratio()
            if actual_w == target_w or sim >= 0.72:
                if parsed.weapon_level is not None and parsed.weapon_level < 80:
                    warn_count += 1
                    items.append(
                        ReviewItem(
                            "武器配置",
                            "warn",
                            f"已装备专武「{entry.weapon_name}」，但武器等级偏低{lv_tag}，建议拉满 Lv.80",
                        )
                    )
                else:
                    pass_count += 1
                    items.append(
                        ReviewItem(
                            "武器配置",
                            "pass",
                            f"已装备推荐专武「{entry.weapon_name}」{lv_tag}",
                        )
                    )
            else:
                warn_count += 1
                items.append(
                    ReviewItem(
                        "武器配置",
                        "warn",
                        f"当前装备「{parsed.weapon_name}」{lv_tag}，非专武「{entry.weapon_name}」",
                        detail=f"抽取建议：{entry.pull_advice.replace(chr(10), ' ')}" if entry.pull_advice else "",
                    )
                )
        else:
            items.append(
                ReviewItem(
                    "武器配置",
                    "info",
                    f"当前武器：{parsed.weapon_name}{lv_tag}",
                    detail=entry.pull_advice.replace("\n", " ") if entry.pull_advice else "",
                )
            )

    # 3. 核心属性 & 副词条门槛点评
    stat_map = parsed.stat_map()
    thresholds = _extract_stat_thresholds(rev.sub_stats)
    threshold_checked = False
    for label, target_val, is_pct in thresholds:
        if label not in stat_map:
            continue
        actual_num = _parse_numeric_value(stat_map[label])
        if actual_num is None:
            continue
        threshold_checked = True
        unit = "%" if is_pct else ""
        diff = actual_num - target_val
        if diff >= 0:
            pass_count += 1
            items.append(
                ReviewItem(
                    f"核心属性·{label}",
                    "pass",
                    f"面板 {label} {stat_map[label]}，已超过一图流门槛 {target_val:g}{unit}（+{diff:g}{unit}）",
                    detail=f"副词条优先级：{' / '.join(rev.sub_stats)}" if rev.sub_stats else "",
                )
            )
        else:
            warn_count += 1
            items.append(
                ReviewItem(
                    f"核心属性·{label}",
                    "warn",
                    f"面板 {label} {stat_map[label]}，距一图流门槛 {target_val:g}{unit} 尚差 {-diff:g}{unit}",
                    detail=f"副词条优先级：{' / '.join(rev.sub_stats)}" if rev.sub_stats else "",
                )
            )

    if not threshold_checked and (rev.sub_stats or rev.main_stats):
        panel_brief = " / ".join(f"{k} {v}" for k, v in parsed.stats[:6])
        items.append(
            ReviewItem(
                "面板与词条参考",
                "info",
                f"面板速览：{panel_brief}" if panel_brief else "已解析基础面板",
                detail=(
                    "副词条优先：" + " → ".join(rev.sub_stats)
                    if rev.sub_stats
                    else "主词条：" + " / ".join(f"{k}:{v}" for k, v in rev.main_stats)
                ),
            )
        )

    # 4. 技能加点点评
    if parsed.skill_levels:
        skill_item = _evaluate_skills(parsed.skill_levels, entry.skill_priority)
        if skill_item.status == "pass":
            pass_count += 1
        elif skill_item.status == "warn":
            warn_count += 1
        items.append(skill_item)

    # 5. 综合练度评级
    if warn_count == 0 and pass_count >= 3:
        grade = "S"
        headline = f"{entry.display_title} · 练度毕业成型，各项核心指标均达标"
    elif warn_count == 0 and pass_count >= 1:
        grade = "A"
        headline = f"{entry.display_title} · 养成方向正确，整体练度良好"
    elif warn_count == 1:
        grade = "A" if pass_count >= 2 else "B"
        headline = f"{entry.display_title} · 主体已成型，仍有 1 项核心可优化空间"
    else:
        grade = "B" if pass_count >= 1 else "C"
        headline = f"{entry.display_title} · 有 {warn_count} 项养成配置偏离一图流推荐，建议按项调整"

    return PanelReview(
        parsed=parsed,
        entry=entry,
        grade=grade,
        headline=headline,
        items=tuple(items),
    )


def analyze_panel_image(
    image_bytes: bytes,
    repo: yituliou.YituliouRepository | None = None,
    extra_characters: Sequence[tuple[str, str]] = (),
) -> PanelReview | None:
    """端到端入口：图片字节 → 预筛 → 本地 OCR → 面板解析 → 一图流点评。非面板图返回 None。"""
    ocr_res = run_ocr(image_bytes)
    if ocr_res is None:
        return None
    boxes, _ = ocr_res
    if not boxes:
        return None
    active_repo = repo if repo is not None else yituliou.load_repository()
    parsed = parse_panel_ocr(boxes, active_repo, extra_characters=extra_characters)
    if parsed is None:
        return None
    return evaluate_panel(parsed, active_repo)


def render_review_text(review: PanelReview) -> str:
    """纯文本点评输出（无 T2I 或渲染失败时使用）。"""
    p = review.parsed
    meta_parts = [
        f"Lv.{p.level}" if p.level else "",
        f"综合评分 {p.total_score}" if p.total_score else "",
        f"练度评级 {review.grade}",
    ]
    lines = [
        f"【面板自动点评 · {p.character_name}】",
        " · ".join(x for x in meta_parts if x),
        review.headline,
        "",
    ]
    icon_map = {"pass": "✅", "warn": "⚠️", "info": "💡"}
    for item in review.items:
        lines.append(f"{icon_map.get(item.status, '·')} {item.category}：{item.summary}")
        if item.detail:
            lines.append(f"   {item.detail}")

    if review.entry is not None:
        if review.entry.rotation:
            order = f"（{review.entry.action_order}）" if review.entry.action_order else ""
            lines.append("")
            lines.append(f"■ 推荐循环{order}：{' / '.join(review.entry.rotation)}")
        ver = f"v{review.entry.version} · " if review.entry.version else ""
        lines.append(f"{ver}对标数据：{review.entry.author}《磨练欲望：30S就能看懂的一图流攻略》")
    return "\n".join(lines)


__all__ = [
    "OcrBox",
    "ParsedPanel",
    "ReviewItem",
    "PanelReview",
    "ocr_available",
    "passes_size_prefilter",
    "run_ocr",
    "parse_panel_ocr",
    "evaluate_panel",
    "analyze_panel_image",
    "render_review_text",
]
