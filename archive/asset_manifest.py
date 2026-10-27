"""素材清单：读 ``data/assets/manifest.json``，不做任何匹配判断。

清单是**独立文件**，不写进 ``p5x_zh.sqlite3`` ——
资料库是只读的，为了加一张图去改库结构，等于把「只读」这条底线拆了。

清单缺失、损坏、字段不对，都不影响插件启动：当作「没有任何素材」处理，
卡片退回占位图。素材能不能用是锦上添花，查询能不能用才是底线。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST = Path("assets") / "manifest.json"
FALLBACK_DIR = "fallback"
FALLBACK_CHARACTER = "character.webp"
FALLBACK_WEAPON = "weapon.webp"
FALLBACK_ARCHIVE = "archive.webp"

SOURCE_STATUS_VALUES = ("official", "community", "unavailable")


@dataclass(frozen=True)
class AssetEntry:
    """一条素材记录。relative_path 为空表示「有登记但没有文件」。"""

    relative_path: str = ""
    source_url: str = ""
    source_status: str = "unavailable"

    @property
    def available(self) -> bool:
        return bool(self.relative_path)


@dataclass(frozen=True)
class CharacterAssets:
    portrait: AssetEntry = field(default_factory=AssetEntry)
    background: AssetEntry = field(default_factory=AssetEntry)

    @property
    def available(self) -> bool:
        return self.portrait.available or self.background.available


@dataclass(frozen=True)
class AssetManifest:
    """一份只读素材清单。root 是清单所在目录，用来解析相对路径。"""

    root: Path
    characters: Mapping[str, CharacterAssets] = field(default_factory=dict)
    weapons: Mapping[str, AssetEntry] = field(default_factory=dict)
    loaded: bool = False
    source: str = ""

    @property
    def fallback_dir(self) -> Path:
        return self.root / FALLBACK_DIR

    def character(self, *keys: str) -> CharacterAssets:
        for key in keys:
            if key and key in self.characters:
                return self.characters[key]
        return CharacterAssets()

    def weapon(self, *keys: str) -> AssetEntry:
        for key in keys:
            if key and key in self.weapons:
                return self.weapons[key]
        return AssetEntry()

    def counts(self) -> dict[str, int]:
        return {
            "characters": len(self.characters),
            "characters_with_portrait": sum(
                1 for item in self.characters.values() if item.portrait.available
            ),
            "weapons": len(self.weapons),
            "weapons_with_image": sum(1 for item in self.weapons.values() if item.available),
        }


EMPTY_MANIFEST = AssetManifest(root=Path("."), loaded=False)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_relative(root: Path, raw: str) -> str:
    """只接受清单目录内的相对路径，挡掉 ``..`` 和绝对路径。"""
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.is_absolute():
        logger.warning("素材清单里出现绝对路径，已忽略：%s", raw)
        return ""
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        logger.warning("素材路径越界，已忽略：%s", raw)
        return ""
    return candidate.as_posix()


def _entry(root: Path, raw: Any, path_keys: tuple[str, ...]) -> AssetEntry:
    if not isinstance(raw, dict):
        return AssetEntry()
    relative = ""
    for key in path_keys:
        relative = _safe_relative(root, _text(raw.get(key)))
        if relative:
            break
    status = _text(raw.get("source_status")).lower()
    if status not in SOURCE_STATUS_VALUES:
        # 没登记状态就当「来源不明」，绝不默认成 official
        status = "unavailable"
    return AssetEntry(
        relative_path=relative,
        source_url=_text(raw.get("source_url")),
        source_status=status,
    )


def parse_manifest(data: Any, root: str | Path) -> AssetManifest:
    root_path = Path(root)
    if not isinstance(data, dict):
        logger.warning("素材清单不是对象，按空清单处理")
        return AssetManifest(root=root_path)

    characters: dict[str, CharacterAssets] = {}
    raw_characters = data.get("characters")
    if isinstance(raw_characters, dict):
        for key, value in raw_characters.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            characters[key] = CharacterAssets(
                portrait=_entry(root_path, value, ("portrait", "image")),
                background=_entry(root_path, value, ("background",)),
            )

    weapons: dict[str, AssetEntry] = {}
    raw_weapons = data.get("weapons")
    if isinstance(raw_weapons, dict):
        for key, value in raw_weapons.items():
            if isinstance(key, str):
                weapons[key] = _entry(root_path, value, ("image", "portrait"))

    return AssetManifest(
        root=root_path,
        characters=characters,
        weapons=weapons,
        loaded=True,
        source=str(root_path),
    )


def load_manifest(path: str | Path | None) -> AssetManifest:
    """读取清单。任何问题都降级成空清单，不抛异常。"""
    if path is None:
        return EMPTY_MANIFEST
    manifest_path = Path(path)
    if not manifest_path.is_file():
        logger.info("素材清单不存在，卡片将使用占位图：%s", manifest_path)
        return AssetManifest(root=manifest_path.parent)

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("素材清单读取失败（%s），卡片将使用占位图", type(exc).__name__)
        return AssetManifest(root=manifest_path.parent)

    manifest = parse_manifest(data, manifest_path.parent)
    if not manifest.loaded:
        return AssetManifest(root=manifest_path.parent)
    logger.info(
        "素材清单已加载：角色 %d（含立绘 %d）／武器 %d（含图 %d）",
        manifest.counts()["characters"],
        manifest.counts()["characters_with_portrait"],
        manifest.counts()["weapons"],
        manifest.counts()["weapons_with_image"],
    )
    return manifest


__all__ = [
    "AssetEntry",
    "CharacterAssets",
    "AssetManifest",
    "EMPTY_MANIFEST",
    "load_manifest",
    "parse_manifest",
    "DEFAULT_MANIFEST",
    "FALLBACK_CHARACTER",
    "FALLBACK_WEAPON",
    "FALLBACK_ARCHIVE",
    "SOURCE_STATUS_VALUES",
]
