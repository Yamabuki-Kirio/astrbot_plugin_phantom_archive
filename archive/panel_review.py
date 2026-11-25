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
import math
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
    "精力恢复": "精力回复",
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
    core_targets: tuple[tuple[str, str], ...] = ()
    awareness: int | None = None
    weapon_mod: int | None = None

    def stat_map(self) -> dict[str, str]:
        return dict(self.stats)

    def target_map(self) -> dict[str, str]:
        return dict(self.core_targets)


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
            "awareness": self.parsed.awareness,
            "weapon_mod": self.parsed.weapon_mod,
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


def _run_stats_crop_ocr(image_bytes: bytes) -> list[OcrBox]:
    """对左侧属性框区域做 2× 放大裁剪再精扫一次，补全首行易漏数字（如生命值）。"""
    ocr = _get_ocr()
    if ocr is None:
        return []
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            w, h = img.size
            x0, y0, x1, y1 = int(w * 0.10), int(h * 0.24), int(w * 0.46), int(h * 0.52)
            crop = img.crop((x0, y0, x1, y1))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
    except Exception:  # noqa: BLE001
        return []

    with _OCR_LOCK:
        result, _ = ocr(buf.getvalue())
    if not result:
        return []

    cw, ch = crop.width, crop.height
    rx0, ry0, rw, rh = 0.10, 0.24, 0.36, 0.28
    boxes: list[OcrBox] = []
    for raw_box, text, score in result:
        clean = str(text or "").strip()
        if not clean:
            continue
        xs = [float(pt[0]) / cw for pt in raw_box]
        ys = [float(pt[1]) / ch for pt in raw_box]
        boxes.append(
            OcrBox(
                text=clean,
                score=float(score),
                nx0=rx0 + min(xs) * rw,
                ny0=ry0 + min(ys) * rh,
                nx1=rx0 + max(xs) * rw,
                ny1=ry0 + max(ys) * rh,
            )
        )
    return boxes


def _parse_core_targets(boxes: Sequence[OcrBox]) -> tuple[tuple[str, str], ...]:
    """提取右侧「核心属性目标」栏位（nx: 0.65~0.95, ny: 0.36~0.64）中的目标数值。"""
    region = [b for b in boxes if 0.65 <= b.nx0 <= 0.95 and 0.36 <= b.ny0 <= 0.64]
    if not region:
        return ()
    labels: list[tuple[float, str]] = []
    values: list[tuple[float, str]] = []
    for b in region:
        if "核心属性" in b.text:
            continue
        cleaned = _clean_ocr_label(b.text)
        matched_label = next((lb for lb in STAT_LABELS if lb in cleaned), None)
        if matched_label is not None:
            labels.append((b.ncy, matched_label))
        elif _is_stat_value(b.text):
            values.append((b.ncy, b.text.strip()))
    if not labels or not values:
        return ()
    # 右侧目标栏略微倾斜，按纵坐标从上到下顺序一一配对最稳健
    labels.sort(key=lambda item: item[0])
    values.sort(key=lambda item: item[0])
    pairs: list[tuple[str, str]] = []
    for (_, lb), (_, val) in zip(labels, values):
        pairs.append((lb, val))
    return tuple(pairs)


def _detect_ring_lit_count(
    arr,
    lum,
    w: int,
    h: int,
    search_box: tuple[int, int, int, int],
    r_in: float,
    r_out: float,
    angle_offset_deg: float,
) -> int | None:
    """定位 6 瓣空心环（意识六芒星 / 武器改造六瓣环）并统计亮白瓣数（0~6）。"""
    import numpy as np

    sx0 = max(3, min(w - 4, search_box[0]))
    sy0 = max(3, min(h - 4, search_box[1]))
    sx1 = max(sx0 + 1, min(w - 3, search_box[2]))
    sy1 = max(sy0 + 1, min(h - 3, search_box[3]))

    angles = np.linspace(0.0, 2.0 * math.pi, 18, endpoint=False)
    r_mid = (r_in + r_out) / 2.0
    best_score = -1e9
    best_xy: tuple[int, int] | None = None

    step = 2 if (sx1 - sx0) * (sy1 - sy0) > 2500 else 1
    for cy in range(sy0, sy1, step):
        for cx in range(sx0, sx1, step):
            if lum[cy, cx] > 55.0:
                continue
            inner = float(lum[cy - 2 : cy + 3, cx - 2 : cx + 3].mean())
            if inner > 65.0:
                continue
            ring_vals = []
            for a in angles:
                ca, sa = math.cos(a), math.sin(a)
                for r in (r_in, r_mid):
                    px, py = int(round(cx + r * ca)), int(round(cy + r * sa))
                    if 0 <= px < w and 0 <= py < h:
                        ring_vals.append(float(lum[py, px]))
            if not ring_vals:
                continue
            score = (sum(ring_vals) / len(ring_vals)) - inner * 1.5
            if score > best_score:
                best_score = score
                best_xy = (cx, cy)

    if best_xy is None or best_score < 35.0:
        return None

    cx, cy = best_xy
    lit = 0
    for i in range(6):
        a0 = math.radians(angle_offset_deg + i * 60.0 - 18.0)
        a1 = math.radians(angle_offset_deg + i * 60.0 + 18.0)
        lums = []
        sats = []
        for a in np.linspace(a0, a1, 9):
            ca, sa = math.cos(a), math.sin(a)
            for r in np.linspace(r_in, r_out, 7):
                px, py = int(round(cx + r * ca)), int(round(cy + r * sa))
                if 0 <= px < w and 0 <= py < h:
                    R, G, B = arr[py, px]
                    lums.append(0.299 * R + 0.587 * G + 0.114 * B)
                    sats.append(max(R, G, B) - min(R, G, B))
        if not lums:
            continue
        white_pct = sum(1 for lv, sv in zip(lums, sats) if lv >= 180.0 and sv <= 48.0) / len(lums)
        p75 = float(np.percentile(lums, 75))
        if white_pct >= 0.30 and p75 >= 185.0:
            lit += 1
    return lit


def _detect_awareness_and_weapon_mod(
    image_bytes: bytes, boxes: Sequence[OcrBox]
) -> tuple[int | None, int | None]:
    """基于相对 OCR 锚点与像素亮度，纯本地识别角色意识（0~6 命）与武器改造等级（0~6 改）。"""
    try:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            arr = np.asarray(rgb, dtype=np.float32)
    except Exception:  # noqa: BLE001
        return None, None

    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]

    header_lv = None
    weapon_lv = None
    for b in boxes:
        if not re.match(r"^lv\.?\s*\d{1,3}$", b.text.strip(), re.I):
            continue
        if b.nx0 < 0.42 and b.ny0 < 0.28 and header_lv is None:
            header_lv = b
        elif 0.27 <= b.nx0 <= 0.48 and 0.68 <= b.ny0 <= 0.85 and weapon_lv is None:
            weapon_lv = b

    awareness: int | None = None
    if header_lv is not None:
        x1 = header_lv.nx1 * w
        cy_lv = header_lv.ncy * h
        lv_h = max(8.0, (header_lv.ny1 - header_lv.ny0) * h)
        search = (
            int(x1 - 0.1 * lv_h),
            int(cy_lv - 2.3 * lv_h),
            int(x1 + 2.2 * lv_h),
            int(cy_lv + 0.2 * lv_h),
        )
        awareness = _detect_ring_lit_count(
            arr, lum, w, h, search, r_in=lv_h * 0.45, r_out=lv_h * 1.08, angle_offset_deg=-75.0
        )

    weapon_mod: int | None = None
    if weapon_lv is not None:
        x1 = weapon_lv.nx1 * w
        y0 = weapon_lv.ny0 * h
        y1 = weapon_lv.ny1 * h
        lv_h = max(8.0, y1 - y0)
        search = (
            int(x1 + 2.8 * lv_h),
            int(y0 - 2.0 * lv_h),
            int(x1 + 6.6 * lv_h),
            int(y1 - 0.3 * lv_h),
        )
        weapon_mod = _detect_ring_lit_count(
            arr, lum, w, h, search, r_in=lv_h * 0.48, r_out=lv_h * 1.10, angle_offset_deg=-60.0
        )

    return awareness, weapon_mod


def parse_panel_ocr(
    boxes: Sequence[OcrBox],
    repo: yituliou.YituliouRepository,
    extra_characters: Sequence[tuple[str, str]] = (),
    stats_override_boxes: Sequence[OcrBox] | None = None,
    awareness: int | None = None,
    weapon_mod: int | None = None,
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

    stats = _parse_stats(stats_override_boxes if stats_override_boxes else boxes)
    if stats_override_boxes:
        # 若精扫缺某项，用全图结果补齐
        merged = dict(_parse_stats(boxes))
        merged.update(dict(stats))
        stats = tuple((lb, merged[lb]) for lb in STAT_LABELS if lb in merged)

    core_targets = _parse_core_targets(boxes)
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
        core_targets=core_targets,
        awareness=awareness,
        weapon_mod=weapon_mod,
    )


# ------------------------------------------------------------------ 一图流对标点评


def _normalize_rev_set(text: str) -> str:
    return re.sub(r"[\s【】\[\]《》&＆+＋·•・]", "", text or "")


@dataclass(frozen=True)
class StatThreshold:
    label: str
    value: float
    is_pct: bool
    op: str  # ">" or "<"
    tier: str  # "常规" | "无心象" | "6意识"


def _extract_stat_thresholds(sub_stats: Sequence[str]) -> list[StatThreshold]:
    """从一图流副词条中提取全部数值门槛（含常规档 / 无心象档 / 6意识档 / 上限约束）。

    示例：
      ['攻击力＞5164', '无心象＞4684', '6意识＞5452']
      ['攻击力＞3852（无心像＞3502）', '暴击效果＞70.4%（无心像＞64%）']
      ['①暴击（满爆）', '③效果命中（＜150%）']
    """
    thresholds: list[StatThreshold] = []
    last_label: str | None = None
    pat_labeled = re.compile(
        r"([\u4e00-\u9fa5]{2,4})\s*[（(]?\s*([>＞≥>=<＜≤]+)\s*(\d+(?:\.\d+)?)(%?)"
    )
    pat_tier = re.compile(
        r"(无心[象像]|6意识|满意识)\s*([>＞≥>=<＜≤]+)\s*(\d+(?:\.\d+)?)(%?)"
    )

    for line in sub_stats:
        line_last_label = last_label
        # 1. 先匹配带属性名的主门槛（一条里可能有多个，如「攻击力＞3852（无心像＞3502）」）
        for m in pat_labeled.finditer(line):
            raw_label, raw_op, num_str, pct = m.groups()
            canonical = STAT_ALIASES.get(raw_label)
            if canonical is None:
                continue
            op = "<" if any(c in raw_op for c in "<＜≤") else ">"
            thresholds.append(StatThreshold(canonical, float(num_str), bool(pct), op, "常规"))
            line_last_label = canonical
            last_label = canonical

        # 2. 再匹配归属于最近属性的「无心象 / 6意识 / 满意识」档位
        if line_last_label is not None:
            for m in pat_tier.finditer(line):
                raw_tier, raw_op, num_str, pct = m.groups()
                tier = "无心象" if "无心" in raw_tier else "6意识"
                op = "<" if any(c in raw_op for c in "<＜≤") else ">"
                thresholds.append(StatThreshold(line_last_label, float(num_str), bool(pct), op, tier))

    return thresholds


def _fmt_num(val: float, is_pct: bool) -> str:
    text = f"{val:g}"
    return f"{text}%" if is_pct else text


def _find_weapon_upgrade_rate(entry: yituliou.YituliouEntry) -> str:
    for r in entry.upgrade_rates:
        if any(k in r.label for k in ("五星", "专武", "5星")):
            return f"{r.label} {r.value}"
    return ""


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

    # 2. 武器与改造等级点评
    if parsed.weapon_name:
        mod_part = f" · {parsed.weapon_mod}改" if parsed.weapon_mod is not None else ""
        lv_part = f"Lv.{parsed.weapon_level}" if parsed.weapon_level else ""
        lv_tag = f"（{lv_part}{mod_part}）" if (lv_part or mod_part) else ""
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
                    high_mod = (
                        "（满改毕业）"
                        if parsed.weapon_mod == 6
                        else (f"（高改造 {parsed.weapon_mod}改）" if (parsed.weapon_mod or 0) >= 3 else "")
                    )
                    items.append(
                        ReviewItem(
                            "武器配置",
                            "pass",
                            f"已装备推荐专武「{entry.weapon_name}」{lv_tag}{high_mod}",
                        )
                    )
            else:
                warn_count += 1
                rate_hint = _find_weapon_upgrade_rate(entry)
                detail_parts = []
                if entry.pull_advice:
                    detail_parts.append(f"抽取建议：{entry.pull_advice.replace(chr(10), ' ')}")
                if rate_hint:
                    detail_parts.append(f"专武提升参考：{rate_hint}")
                items.append(
                    ReviewItem(
                        "武器配置",
                        "warn",
                        f"当前装备「{parsed.weapon_name}」{lv_tag}，非专武「{entry.weapon_name}」",
                        detail=" ｜ ".join(detail_parts),
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

    # 3. 核心属性 & 一图流数值门槛（含多档位：无心象 / 常规 / 6意识）& 游戏内核心目标
    stat_map = parsed.stat_map()
    target_map = parsed.target_map()
    thresholds = _extract_stat_thresholds(rev.sub_stats)
    in_battle_note = "（一图流为进图数值，面板为站街值）" if ("进图" in rev.note or "图内" in rev.note or "战斗中" in " ".join(rev.sub_stats)) else ""

    # 按属性名分组，避免同一属性拆出 3 张重复卡片
    grouped_thresholds: dict[str, list[StatThreshold]] = {}
    for th in thresholds:
        grouped_thresholds.setdefault(th.label, []).append(th)

    threshold_checked = False
    for label, th_list in grouped_thresholds.items():
        if label not in stat_map:
            # 面板未露出该属性（例如解明角色不显示暴击效果、或 DPS 隐藏属性），列出目标供核对
            tier_desc = " / ".join(
                f"{t.tier}{t.op}{_fmt_num(t.value, t.is_pct)}" for t in th_list
            )
            items.append(
                ReviewItem(
                    f"推荐属性·{label}",
                    "info",
                    f"一图流推荐 {label}：{tier_desc}{in_battle_note}",
                    detail="该属性未在分享面板首页直接显示，可在游戏内点「详情」核对",
                )
            )
            continue

        actual_num = _parse_numeric_value(stat_map[label])
        if actual_num is None:
            continue
        threshold_checked = True

        # 先看上限约束（如 效果命中＜150%）
        upper_ths = [t for t in th_list if t.op == "<"]
        lower_ths = [t for t in th_list if t.op == ">"]

        if upper_ths and not lower_ths:
            t = upper_ths[0]
            if actual_num <= t.value:
                pass_count += 1
                items.append(
                    ReviewItem(
                        f"核心属性·{label}",
                        "pass",
                        f"面板 {label} {stat_map[label]}，未超上限 {_fmt_num(t.value, t.is_pct)}{in_battle_note}",
                    )
                )
            else:
                warn_count += 1
                items.append(
                    ReviewItem(
                        f"核心属性·{label}",
                        "warn",
                        f"面板 {label} {stat_map[label]}，超出推荐上限 {_fmt_num(t.value, t.is_pct)}（溢出 +{_fmt_num(actual_num - t.value, t.is_pct)}）",
                    )
                )
            continue

        # 下限约束：按阈值从低到高排序（通常 无心象 < 常规 < 6意识）
        lower_ths.sort(key=lambda t: t.value)
        base_th = next((t for t in lower_ths if t.tier == "常规"), lower_ths[0])
        highest_met = [t for t in lower_ths if actual_num >= t.value]
        tier_summary = " / ".join(f"{t.tier}>{_fmt_num(t.value, t.is_pct)}" for t in lower_ths)
        game_target_hint = f"；游戏目标 {target_map[label]}" if label in target_map else ""

        if actual_num >= base_th.value:
            pass_count += 1
            top = highest_met[-1]
            diff = actual_num - top.value
            extra_next = ""
            unmet = [t for t in lower_ths if actual_num < t.value]
            if unmet:
                nxt = unmet[0]
                extra_next = f"（距 {nxt.tier}档 {_fmt_num(nxt.value, nxt.is_pct)} 差 {_fmt_num(nxt.value - actual_num, nxt.is_pct)}）"
            items.append(
                ReviewItem(
                    f"核心属性·{label}",
                    "pass",
                    f"面板 {label} {stat_map[label]}，已达「{top.tier}档」{_fmt_num(top.value, top.is_pct)}（+{_fmt_num(diff, top.is_pct)}）{extra_next}",
                    detail=f"一图流分档：{tier_summary}{game_target_hint}{in_battle_note}",
                )
            )
        elif highest_met:
            # 过了无心象档，但未到常规心象档
            pass_count += 1
            met = highest_met[-1]
            gap = base_th.value - actual_num
            items.append(
                ReviewItem(
                    f"核心属性·{label}",
                    "info",
                    f"面板 {label} {stat_map[label]}，已达「{met.tier}档」{_fmt_num(met.value, met.is_pct)}，距常规档 {_fmt_num(base_th.value, base_th.is_pct)} 尚差 {_fmt_num(gap, base_th.is_pct)}",
                    detail=f"一图流分档：{tier_summary}{game_target_hint}{in_battle_note}",
                )
            )
        else:
            warn_count += 1
            first = lower_ths[0]
            gap = first.value - actual_num
            items.append(
                ReviewItem(
                    f"核心属性·{label}",
                    "warn",
                    f"面板 {label} {stat_map[label]}，距一图流门槛 {_fmt_num(first.value, first.is_pct)} 尚差 {_fmt_num(gap, first.is_pct)}",
                    detail=f"一图流分档：{tier_summary}{game_target_hint}{in_battle_note}",
                )
            )

    # 游戏内「核心属性目标」核对（截图右侧官方目标条）
    if target_map:
        met_parts = []
        unmet_parts = []
        for t_label, t_val_str in parsed.core_targets:
            if t_label not in stat_map:
                continue
            a_num = _parse_numeric_value(stat_map[t_label])
            t_num = _parse_numeric_value(t_val_str)
            if a_num is None or t_num is None:
                continue
            if a_num >= t_num:
                met_parts.append(f"{t_label} {stat_map[t_label]}/{t_val_str}")
            else:
                unmet_parts.append(f"{t_label} {stat_map[t_label]}/{t_val_str}(差{t_num - a_num:g})")
        if unmet_parts:
            warn_count += 1
            items.append(
                ReviewItem(
                    "游戏核心目标",
                    "warn",
                    "未达标：" + "、".join(unmet_parts),
                    detail=("已达标：" + " / ".join(met_parts)) if met_parts else "",
                )
            )
        elif met_parts:
            if not threshold_checked:
                pass_count += 1
            items.append(
                ReviewItem(
                    "游戏核心目标",
                    "pass",
                    "全部达成：" + " / ".join(met_parts),
                    detail=(
                        "一图流词条优先：" + " → ".join(rev.sub_stats)
                        if (not threshold_checked and rev.sub_stats)
                        else ""
                    ),
                )
            )
    elif not threshold_checked and (rev.sub_stats or rev.main_stats):
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

    # 4.5 角色意识（命座）点评
    if parsed.awareness is not None:
        if parsed.awareness >= 6:
            pass_count += 1
            items.append(
                ReviewItem(
                    "意识（命座）",
                    "pass",
                    f"已达 6 意识（满命完全体）",
                    detail=f"抽取建议参考：{entry.pull_advice.replace(chr(10), ' ')}" if entry.pull_advice else "",
                )
            )
        elif parsed.awareness >= 3:
            pass_count += 1
            items.append(
                ReviewItem(
                    "意识（命座）",
                    "pass",
                    f"高命配置：{parsed.awareness} 意识（{parsed.awareness} 命）",
                    detail=f"抽取建议参考：{entry.pull_advice.replace(chr(10), ' ')}" if entry.pull_advice else "",
                )
            )
        else:
            items.append(
                ReviewItem(
                    "意识（命座）",
                    "info",
                    f"当前 {parsed.awareness} 意识（{parsed.awareness} 命）",
                    detail=f"抽取建议参考：{entry.pull_advice.replace(chr(10), ' ')}" if entry.pull_advice else "",
                )
            )

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
    """端到端入口：图片字节 → 预筛 → 本地 OCR → 属性区精扫 → 面板解析 → 一图流点评。"""
    ocr_res = run_ocr(image_bytes)
    if ocr_res is None:
        return None
    boxes, _ = ocr_res
    if not boxes or _count_anchors(boxes) < MIN_ANCHOR_HITS:
        return None
    active_repo = repo if repo is not None else yituliou.load_repository()
    # 确认是 P5X 面板后，对左侧属性栏做 2× 局部精扫补齐易漏数字（如生命值），并识别意识/武器改造环
    stats_crop_boxes = _run_stats_crop_ocr(image_bytes)
    awareness, weapon_mod = _detect_awareness_and_weapon_mod(image_bytes, boxes)
    parsed = parse_panel_ocr(
        boxes,
        active_repo,
        extra_characters=extra_characters,
        stats_override_boxes=stats_crop_boxes or None,
        awareness=awareness,
        weapon_mod=weapon_mod,
    )
    if parsed is None:
        return None
    return evaluate_panel(parsed, active_repo)


def render_review_text(review: PanelReview) -> str:
    """纯文本点评输出（默认主动识图输出与无 T2I 降级均使用它）。"""
    p = review.parsed
    meta_parts = [
        f"Lv.{p.level}" if p.level else "",
        f"{p.awareness}意识" if p.awareness is not None else "",
        f"武器{p.weapon_mod}改" if p.weapon_mod is not None else "",
        f"综合评分 {p.total_score}" if p.total_score else "",
        f"练度评级 {review.grade}",
    ]
    lines = [
        f"【面板自动点评 · {p.character_name}】",
        " · ".join(x for x in meta_parts if x),
        review.headline,
    ]
    if p.stats:
        lines.append("面板速览：" + " / ".join(f"{k} {v}" for k, v in p.stats))
    lines.append("")

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
        if review.entry.tips:
            lines.append(f"■ 实战提醒：{review.entry.tips[0]}")
        ver = f"v{review.entry.version} · " if review.entry.version else ""
        lines.append(f"{ver}对标数据：{review.entry.author}《磨练欲望：30S就能看懂的一图流攻略》")
    return "\n".join(lines)


def build_persona_llm_prompt(review: PanelReview) -> str:
    """构造供当前启用人格（纯文本 LLM，不传图）使用的角色面板点评提示词。

    所有数值均已由本地 RapidOCR 提取并与一图流完成对标，LLM 仅负责以当前人格口吻自然点评。
    """
    p = review.parsed
    facts: list[str] = [
        f"- 角色：{p.character_name}" + (f"（Lv.{p.level}）" if p.level else ""),
    ]
    if p.awareness is not None:
        high_aw = "（高命座）" if p.awareness >= 3 else ""
        facts.append(f"- 角色意识（命座）：{p.awareness}命（{p.awareness}意识）{high_aw}")
    if p.total_score:
        facts.append(f"- 游戏内综合评分：{p.total_score}")
    if p.stats:
        facts.append("- 面板属性：" + "、".join(f"{k} {v}" for k, v in p.stats))
    if p.core_targets:
        facts.append("- 游戏核心属性目标：" + "、".join(f"{k} {v}" for k, v in p.core_targets))
    if p.skill_levels:
        labels = ("①技能", "②技能", "③技能", "战斗技巧")
        facts.append(
            "- 技能等级：" + " / ".join(f"{labels[i]} Lv.{lv}" for i, lv in enumerate(p.skill_levels[:4]))
        )
    if p.revelation_set:
        facts.append(f"- 当前启示卡：{p.revelation_set}")
    if p.weapon_name:
        lv = f" Lv.{p.weapon_level}" if p.weapon_level else ""
        mod = f" · {p.weapon_mod}改" if p.weapon_mod is not None else ""
        facts.append(f"- 当前武器：{p.weapon_name}{lv}{mod}")

    eval_lines: list[str] = [
        f"- 综合练度评级：{review.grade}（{review.headline}）",
    ]
    status_zh = {"pass": "达标", "warn": "可优化", "info": "参考"}
    for it in review.items:
        line = f"- [{status_zh.get(it.status, '参考')}] {it.category}：{it.summary}"
        if it.detail:
            line += f"（{it.detail}）"
        eval_lines.append(line)

    sections = [
        "用户刚刚发送了一张《女神异闻录：夜幕魅影》（P5X）的角色养成面板截图。",
        "系统已通过本地识别完成数据读取与练度对标，以下是严格准确的面板与评估数据：",
        "",
        "【玩家面板数据】",
        *facts,
        "",
        "【练度对标结果】",
        *eval_lines,
        "",
        "【回复要求】",
        "1. 严格保持你当前扮演的人格、语气、称呼、口癖与性格，像真正亲自查看了这张面板一样自然点评，不要跳出人设。",
        "2. 严禁提及“OCR”“识别结果”“一图流攻略”“系统提供的数据”等出戏或机械词，直接把这些当作你自己的眼光和判断。",
        "3. 只点评面板本身的养成练度（属性数值、启示卡套装、武器及改造等级、意识/命座、技能等级的亮点与最值得补强的 1~2 项；若意识达到 3 命及以上或武器达到高改造等级，请自然提及并给予符合人设的评价/赞许）。",
        "4. 不要教实战打法、技能循环、操作手法、排轴或配队教学，也不要编造未给出的数值。",
        "5. 控制在 80~160 字左右，口语自然紧凑，直接输出给用户的最终回复。",
    ]
    return "\n".join(sections)


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
    "build_persona_llm_prompt",
]
