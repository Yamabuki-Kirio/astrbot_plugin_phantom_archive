"""强效召唤《磨练欲望：30S就能看懂的一图流攻略》结构化数据源。

只读加载 ``data/yituliou/characters.json`` 与 ``data/yituliou/tables.json``，
为 ``/p5x角色`` 单图一图流卡片、相关攻略指令与附表查询提供统一数据访问层。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_YITULIOU_DIR = Path(__file__).resolve().parent.parent / "data" / "yituliou"

# 双子三种模式关键词映射，支持「双子反抗」「J&C救援」「双子优越」等直接定位
_MODE_HINTS: dict[str, tuple[str, ...]] = {
    "反抗模式": ("反抗模式", "反抗", "输出模式", "输出"),
    "救援模式": ("救援模式", "救援", "治疗模式", "奶"),
    "优越模式": ("优越模式", "优越", "辅助模式", "拐"),
}


def normalize_key(text: str | None) -> str:
    """与 archive.search 保持一致的宽松归一化。"""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        ch
        for ch in value
        if not ch.isspace() and ch not in "·•・.-_/&＆（）()【】[]「」"
    )


@dataclass(frozen=True)
class ScenarioRating:
    label: str
    stars: str


@dataclass(frozen=True)
class SkillBrief:
    label: str
    text: str


@dataclass(frozen=True)
class UpgradeRate:
    label: str
    value: str


@dataclass(frozen=True)
class RevelationConfig:
    note: str
    primary: tuple[str, ...]
    alternative: tuple[str, ...]
    main_stats: tuple[tuple[str, str], ...]
    sub_stats: tuple[str, ...]


@dataclass(frozen=True)
class StrengthRefRow:
    left_label: str
    left_value: str
    right_label: str
    right_value: str


@dataclass(frozen=True)
class StrengthRef:
    note: str
    rows: tuple[StrengthRefRow, ...]


@dataclass(frozen=True)
class Teammate:
    name: str
    tag: str


@dataclass(frozen=True)
class YituliouEntry:
    id: str
    slug: str
    source_file: str
    name: str
    mode: str
    role: str
    elements: tuple[str, ...]
    obtain: str
    overall_rating: str
    overall_score: str
    aliases: tuple[str, ...]
    scenarios: tuple[ScenarioRating, ...]
    skills_brief: tuple[SkillBrief, ...]
    review: str
    pull_advice: str
    weapon_name: str
    upgrade_rates: tuple[UpgradeRate, ...]
    revelation: RevelationConfig
    skill_priority: str
    strength_ref: StrengthRef
    rotation: tuple[str, ...]
    action_order: str
    teammates: tuple[Teammate, ...]
    tips: tuple[str, ...]
    version: str
    author: str
    footnote: str

    @property
    def display_title(self) -> str:
        return f"{self.name}（{self.mode}）" if self.mode else self.name

    @property
    def is_compact(self) -> bool:
        """常驻合集类条目只有启示卡/循环/加点/推荐指数，无技能简述与提升率。"""
        return not self.skills_brief and not self.upgrade_rates and not self.review

    def as_template_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "display_title": self.display_title,
            "mode": self.mode,
            "role": self.role,
            "elements": list(self.elements),
            "obtain": self.obtain,
            "overall_rating": self.overall_rating,
            "overall_score": self.overall_score,
            "aliases": list(self.aliases),
            "scenarios": [{"label": s.label, "stars": s.stars} for s in self.scenarios],
            "skills_brief": [{"label": s.label, "text": s.text} for s in self.skills_brief],
            "review": self.review,
            "pull_advice": self.pull_advice,
            "weapon_name": self.weapon_name,
            "upgrade_rates": [{"label": u.label, "value": u.value} for u in self.upgrade_rates],
            "revelation": {
                "note": self.revelation.note,
                "primary": list(self.revelation.primary),
                "alternative": list(self.revelation.alternative),
                "main_stats": [
                    {"slot": slot, "stat": stat} for slot, stat in self.revelation.main_stats
                ],
                "sub_stats": list(self.revelation.sub_stats),
            },
            "skill_priority": self.skill_priority,
            "strength_ref": {
                "note": self.strength_ref.note,
                "rows": [
                    {
                        "left_label": r.left_label,
                        "left_value": r.left_value,
                        "right_label": r.right_label,
                        "right_value": r.right_value,
                    }
                    for r in self.strength_ref.rows
                ],
            },
            "rotation": list(self.rotation),
            "action_order": self.action_order,
            "teammates": [{"name": t.name, "tag": t.tag} for t in self.teammates],
            "tips": list(self.tips),
            "version": self.version,
            "author": self.author,
            "footnote": self.footnote,
            "is_compact": self.is_compact,
        }


@dataclass(frozen=True)
class YituliouTable:
    id: str
    aliases: tuple[str, ...]
    source_file: str
    title: str
    subtitle: str
    version: str
    author: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    notes: str

    def as_template_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "version": self.version,
            "author": self.author,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "notes": self.notes,
        }


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_list(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        v = value.strip()
        return (v,) if v else ()
    return tuple(s for item in value if (s := _clean_str(item)))


def _parse_entry(raw: dict[str, Any]) -> YituliouEntry:
    rev = raw.get("revelation") or {}
    main_stats_raw = rev.get("main_stats") or {}
    if isinstance(main_stats_raw, dict):
        slot_order = ("日位", "月位", "星位", "辰位")
        ordered: list[tuple[str, str]] = []
        for slot in slot_order:
            if slot in main_stats_raw and _clean_str(main_stats_raw[slot]):
                ordered.append((slot, _clean_str(main_stats_raw[slot])))
        for k, v in main_stats_raw.items():
            if k not in slot_order and _clean_str(v):
                ordered.append((_clean_str(k), _clean_str(v)))
        main_stats = tuple(ordered)
    else:
        main_stats = ()

    sref = raw.get("strength_ref") or {}
    sref_rows = tuple(
        StrengthRefRow(
            left_label=_clean_str(r.get("left_label")),
            left_value=_clean_str(r.get("left_value")),
            right_label=_clean_str(r.get("right_label")),
            right_value=_clean_str(r.get("right_value")),
        )
        for r in (sref.get("rows") or [])
        if isinstance(r, dict) and (_clean_str(r.get("left_label")) or _clean_str(r.get("right_label")))
    )

    score_raw = raw.get("overall_score")
    score_str = "" if score_raw is None else _clean_str(score_raw)
    rating_str = _clean_str(raw.get("overall_rating"))
    # 若 overall_rating 已包含分数（如「74（★★★）」或「0意识: 66 / 1意识: 82」），不再重复输出 overall_score
    if score_str and score_str in rating_str:
        score_str = ""
    version_str = strip_version_prefix(_clean_str(raw.get("version"))).removesuffix("版本").strip()

    return YituliouEntry(
        id=_clean_str(raw.get("id")),
        slug=_clean_str(raw.get("slug")),
        source_file=_clean_str(raw.get("source_file")),
        name=_clean_str(raw.get("name")),
        mode=_clean_str(raw.get("mode")),
        role=_clean_str(raw.get("role")),
        elements=_clean_list(raw.get("elements")),
        obtain=_clean_str(raw.get("obtain")),
        overall_rating=rating_str,
        overall_score=score_str,
        aliases=_clean_list(raw.get("aliases")),
        scenarios=tuple(
            ScenarioRating(label=_clean_str(s.get("label")), stars=_clean_str(s.get("stars")))
            for s in (raw.get("scenarios") or [])
            if isinstance(s, dict) and _clean_str(s.get("label"))
        ),
        skills_brief=tuple(
            SkillBrief(label=_clean_str(s.get("label")), text=_clean_str(s.get("text")))
            for s in (raw.get("skills_brief") or [])
            if isinstance(s, dict) and (_clean_str(s.get("label")) or _clean_str(s.get("text")))
        ),
        review=_clean_str(raw.get("review")),
        pull_advice=_clean_str(raw.get("pull_advice")),
        weapon_name=_clean_str(raw.get("weapon_name")),
        upgrade_rates=tuple(
            UpgradeRate(label=_clean_str(u.get("label")), value=_clean_str(u.get("value")))
            for u in (raw.get("upgrade_rates") or [])
            if isinstance(u, dict) and _clean_str(u.get("label"))
        ),
        revelation=RevelationConfig(
            note=_clean_str(rev.get("note")),
            primary=_clean_list(rev.get("primary")),
            alternative=_clean_list(rev.get("alternative")),
            main_stats=main_stats,
            sub_stats=_clean_list(rev.get("sub_stats")),
        ),
        skill_priority=_clean_str(raw.get("skill_priority")),
        strength_ref=StrengthRef(note=_clean_str(sref.get("note")), rows=sref_rows),
        rotation=_clean_list(raw.get("rotation")),
        action_order=_clean_str(raw.get("action_order")),
        teammates=tuple(
            Teammate(name=_clean_str(t.get("name")), tag=_clean_str(t.get("tag")))
            for t in (raw.get("teammates") or [])
            if isinstance(t, dict) and (_clean_str(t.get("name")) or _clean_str(t.get("tag")))
        ),
        tips=_clean_list(raw.get("tips")),
        version=version_str,
        author=_clean_str(raw.get("author")) or "强效召唤",
        footnote=_clean_str(raw.get("footnote")),
    )


def _parse_table(raw: dict[str, Any]) -> YituliouTable:
    return YituliouTable(
        id=_clean_str(raw.get("id")),
        aliases=_clean_list(raw.get("aliases")),
        source_file=_clean_str(raw.get("source_file")),
        title=_clean_str(raw.get("title")),
        subtitle=_clean_str(raw.get("subtitle")),
        version=_clean_str(raw.get("version")),
        author=_clean_str(raw.get("author")) or "强效召唤",
        columns=_clean_list(raw.get("columns")),
        rows=tuple(
            tuple(_clean_str(cell) for cell in row)
            for row in (raw.get("rows") or [])
            if isinstance(row, list)
        ),
        notes=_clean_str(raw.get("notes")),
    )


def _extract_mode_hint(term: str) -> tuple[str, str]:
    """从查询词中剥离模式后缀（如「双子反抗」→ ('双子', '反抗模式')）。"""
    stripped = (term or "").strip()
    for canonical_mode, hints in _MODE_HINTS.items():
        for hint in hints:
            if stripped.endswith(hint) and len(stripped) > len(hint):
                base = stripped[: -len(hint)].strip(" -·•・/（）()")
                if base:
                    return base, canonical_mode
    return stripped, ""


class YituliouRepository:
    """只读一图流资料库。"""

    def __init__(
        self,
        entries: tuple[YituliouEntry, ...] = (),
        tables: tuple[YituliouTable, ...] = (),
    ):
        self._entries = entries
        self._tables = tables
        self._by_id: dict[str, YituliouEntry] = {e.id: e for e in entries if e.id}
        self._by_slug: dict[str, list[YituliouEntry]] = {}
        self._exact_index: dict[str, list[YituliouEntry]] = {}

        for entry in entries:
            if entry.slug:
                self._by_slug.setdefault(entry.slug, []).append(entry)
            keys = {
                normalize_key(entry.id),
                normalize_key(entry.name),
                normalize_key(entry.display_title),
            }
            if entry.mode:
                keys.add(normalize_key(f"{entry.name}{entry.mode}"))
                for hint in _MODE_HINTS.get(entry.mode, ()):
                    keys.add(normalize_key(f"{entry.name}{hint}"))
            for alias in entry.aliases:
                keys.add(normalize_key(alias))
                if entry.mode:
                    keys.add(normalize_key(f"{alias}{entry.mode}"))
                    for hint in _MODE_HINTS.get(entry.mode, ()):
                        keys.add(normalize_key(f"{alias}{hint}"))
            for key in keys:
                if key:
                    bucket = self._exact_index.setdefault(key, [])
                    if entry not in bucket:
                        bucket.append(entry)

    @classmethod
    def load(cls, data_dir: Path | str | None = None) -> "YituliouRepository":
        root = Path(data_dir) if data_dir is not None else DEFAULT_YITULIOU_DIR
        entries: list[YituliouEntry] = []
        tables: list[YituliouTable] = []

        chars_path = root / "characters.json"
        if chars_path.is_file():
            raw_chars = json.loads(chars_path.read_text(encoding="utf-8"))
            if isinstance(raw_chars, list):
                entries = [_parse_entry(item) for item in raw_chars if isinstance(item, dict)]

        tables_path = root / "tables.json"
        if tables_path.is_file():
            raw_tables = json.loads(tables_path.read_text(encoding="utf-8"))
            if isinstance(raw_tables, list):
                tables = [_parse_table(item) for item in raw_tables if isinstance(item, dict)]

        return cls(entries=tuple(entries), tables=tuple(tables))

    @property
    def entries(self) -> tuple[YituliouEntry, ...]:
        return self._entries

    @property
    def tables(self) -> tuple[YituliouTable, ...]:
        return self._tables

    def find_by_slug(self, slug: str, mode: str = "") -> YituliouEntry | None:
        matches = self._by_slug.get(slug, [])
        if not matches:
            return None
        if mode:
            for item in matches:
                if item.mode == mode:
                    return item
        if len(matches) == 1:
            return matches[0]
        return None

    def find_all_by_slug(self, slug: str) -> tuple[YituliouEntry, ...]:
        return tuple(self._by_slug.get(slug, ()))

    def search(self, term: str) -> tuple[YituliouEntry, ...]:
        """精确或带模式后缀匹配一图流条目。"""
        if not term or not self._entries:
            return ()
        direct_key = normalize_key(term)
        if direct_key in self._exact_index:
            return tuple(self._exact_index[direct_key])

        base_term, mode_hint = _extract_mode_hint(term)
        if mode_hint:
            base_key = normalize_key(base_term)
            if base_key in self._exact_index:
                filtered = [e for e in self._exact_index[base_key] if e.mode == mode_hint]
                if filtered:
                    return tuple(filtered)
        return ()

    def find_table(self, term: str) -> YituliouTable | None:
        key = normalize_key(term)
        if not key:
            return None
        for table in self._tables:
            candidates = [table.id, table.title, *table.aliases]
            if any(normalize_key(c) == key for c in candidates):
                return table
        for table in self._tables:
            candidates = [table.id, table.title, *table.aliases]
            if any(key in normalize_key(c) for c in candidates):
                return table
        return None


@lru_cache(maxsize=4)
def load_repository(data_dir: str = "") -> YituliouRepository:
    return YituliouRepository.load(Path(data_dir) if data_dir else DEFAULT_YITULIOU_DIR)


# ------------------------------------------------------------------ 文本渲染（降级与测试）


def render_yituliou_text(entry: YituliouEntry, max_chars: int = 1800) -> str:
    """将一图流条目渲染为结构化纯文本（无图模式或渲染失败时使用）。"""
    header_meta = " · ".join(
        part
        for part in [
            entry.role,
            "/".join(entry.elements),
            entry.obtain,
        ]
        if part
    )
    rating = (
        f"{entry.overall_score}（{entry.overall_rating}）"
        if entry.overall_score and entry.overall_rating
        else (entry.overall_score or entry.overall_rating)
    )
    lines: list[str] = [f"【{entry.display_title}】"]
    if header_meta:
        lines.append(header_meta)
    if rating:
        lines.append(f"综合推荐：{rating}")
    if entry.aliases:
        lines.append(f"常用称呼：{'、'.join(entry.aliases)}")

    if entry.scenarios:
        lines.append("")
        lines.append("■ 适用场景")
        lines.append(" / ".join(f"{s.label} {s.stars}" for s in entry.scenarios))

    if entry.review:
        lines.append("")
        lines.append("■ 角色简评")
        lines.append(entry.review)

    if entry.pull_advice or entry.upgrade_rates:
        lines.append("")
        lines.append("■ 抽取建议")
        if entry.pull_advice:
            weapon = f"（专武：{entry.weapon_name}）" if entry.weapon_name else ""
            lines.append(f"{entry.pull_advice}{weapon}")
        if entry.upgrade_rates:
            lines.append(" / ".join(f"{u.label} {u.value}" for u in entry.upgrade_rates))

    rev = entry.revelation
    if rev.primary or rev.main_stats or rev.sub_stats:
        lines.append("")
        note = f"（{rev.note}）" if rev.note else ""
        lines.append(f"■ 启示卡{note}")
        if rev.primary:
            lines.append(f"首选：{' / '.join(rev.primary)}")
        if rev.alternative:
            lines.append(f"备选：{' / '.join(rev.alternative)}")
        if rev.main_stats:
            lines.append("主词条：" + " | ".join(f"{k}:{v}" for k, v in rev.main_stats))
        if rev.sub_stats:
            lines.append("副词条：" + " ".join(rev.sub_stats))

    if entry.skill_priority:
        lines.append("")
        lines.append(f"■ 技能升级：{entry.skill_priority}")

    if entry.rotation or entry.action_order:
        lines.append("")
        order = f"（常用行动顺序：{entry.action_order}）" if entry.action_order else ""
        lines.append(f"■ 技能循环{order}")
        lines.extend(entry.rotation)

    if entry.teammates:
        lines.append("")
        lines.append(
            "■ 常用搭配："
            + " / ".join(f"{t.name}({t.tag})" if t.tag else t.name for t in entry.teammates)
        )

    if entry.tips:
        lines.append("")
        lines.append("■ Tips")
        lines.extend(entry.tips)

    footer_parts = [p for p in [f"v{entry.version}" if entry.version else "", f"By {entry.author}"] if p]
    if footer_parts:
        lines.append("")
        lines.append(" · ".join(footer_parts))

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def render_table_list_text(tables: tuple[YituliouTable, ...]) -> str:
    if not tables:
        return "当前未收录一图流附表。"
    lines = ["当前收录一图流附表（发送 /p5x榜单 <名称或序号> 查看）："]
    for idx, table in enumerate(tables, start=1):
        ver = f"（v{table.version}）" if table.version else ""
        lines.append(f"{idx}. {table.title}{ver}")
    return "\n".join(lines)


def render_table_text(table: YituliouTable, max_rows: int = 20) -> str:
    lines = [f"【{table.title}】"]
    if table.subtitle:
        lines.append(table.subtitle)
    if table.columns:
        lines.append(" | ".join(table.columns))
    for row in table.rows[:max_rows]:
        lines.append(" | ".join(row))
    if len(table.rows) > max_rows:
        lines.append(f"… 共 {len(table.rows)} 行，图片模式可查看完整表格")
    if table.notes:
        lines.append("")
        lines.append(table.notes)
    return "\n".join(lines)


def yituliou_guide_sections(entry: YituliouEntry, guide_type: str) -> list[tuple[str, list[str]]]:
    """按攻略类别从一图流提取结构化段落 [(heading, lines), ...]，空则表示该项需回退数据库。"""
    sections: list[tuple[str, list[str]]] = []
    if guide_type == "review":
        summary: list[str] = []
        rating = (
            f"{entry.overall_score}（{entry.overall_rating}）"
            if entry.overall_score and entry.overall_rating
            else (entry.overall_score or entry.overall_rating)
        )
        if rating:
            summary.append(f"综合推荐指数：{rating}")
        if entry.scenarios:
            summary.extend(f"· {s.label}：{s.stars}" for s in entry.scenarios)
        if summary:
            sections.append(("适用场景与推荐度", summary))
        if entry.review:
            sections.append(("角色简评", [entry.review]))
        if entry.strength_ref.rows:
            ref_lines = []
            for r in entry.strength_ref.rows:
                pair = f"· {r.left_label} {r.left_value}"
                if r.right_label or r.right_value:
                    pair += f" | {r.right_label} {r.right_value}"
                ref_lines.append(pair)
            if entry.strength_ref.note:
                ref_lines.append(f"说明：{entry.strength_ref.note}")
            sections.append(("参考强度", ref_lines))

    elif guide_type == "setting":
        pull_lines: list[str] = []
        if entry.pull_advice:
            weapon = f"（专武：{entry.weapon_name}）" if entry.weapon_name else ""
            pull_lines.append(f"抽取建议：{entry.pull_advice}{weapon}")
        if entry.upgrade_rates:
            pull_lines.extend(f"· {u.label}：{u.value}" for u in entry.upgrade_rates)
        if pull_lines:
            sections.append(("抽取建议与参考提升率", pull_lines))

        rev = entry.revelation
        rev_lines: list[str] = []
        if rev.primary:
            rev_lines.append(f"首选：{' / '.join(rev.primary)}")
        if rev.alternative:
            rev_lines.append(f"备选：{' / '.join(rev.alternative)}")
        if rev.main_stats:
            rev_lines.append("主词条：" + " | ".join(f"{k}:{v}" for k, v in rev.main_stats))
        if rev.sub_stats:
            rev_lines.append("副词条：" + " ".join(rev.sub_stats))
        if rev_lines:
            title = f"启示卡（{rev.note}）" if rev.note else "启示卡配置"
            sections.append((title, rev_lines))

        if entry.skill_priority:
            sections.append(("技能升级", [entry.skill_priority]))

    elif guide_type == "operation":
        rot_lines: list[str] = []
        if entry.action_order:
            rot_lines.append(f"常用行动顺序：{entry.action_order}")
        rot_lines.extend(f"· {line}" for line in entry.rotation)
        if rot_lines:
            sections.append(("技能循环", rot_lines))
        if entry.tips:
            sections.append(("实战 Tips", list(entry.tips)))

    elif guide_type == "party":
        if entry.teammates:
            team_lines = [
                f"· {t.name}（{t.tag}）" if t.tag else f"· {t.name}"
                for t in entry.teammates
            ]
            sections.append(("常用搭配", team_lines))

    return sections


def render_yituliou_guide_text(
    entry: YituliouEntry, guide_title: str, sections: list[tuple[str, list[str]]]
) -> str:
    lines = [f"【{entry.display_title} · {guide_title}】"]
    for heading, body in sections:
        lines.append("")
        lines.append(f"■ {heading}")
        lines.extend(body)
    ver = f"v{entry.version} · " if entry.version else ""
    lines.append("")
    lines.append(f"{ver}数据来源：{entry.author}《磨练欲望：30S就能看懂的一图流攻略》")
    return "\n".join(lines)


def strip_version_prefix(text: str) -> str:
    return re.sub(r"^v+", "", (text or "").strip(), flags=re.IGNORECASE)


__all__ = [
    "DEFAULT_YITULIOU_DIR",
    "YituliouEntry",
    "YituliouTable",
    "YituliouRepository",
    "load_repository",
    "normalize_key",
    "render_yituliou_text",
    "render_table_list_text",
    "render_table_text",
]
