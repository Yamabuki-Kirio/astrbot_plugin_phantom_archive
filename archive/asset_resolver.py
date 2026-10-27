"""素材匹配与回退：把「角色/武器」翻译成一张能用的 Data URI。

匹配顺序固定，且**只按标识符匹配**，不做模糊猜测：

    角色：slug → source_key
    武器：``角色:武器`` → ``武器``

拿不到就按规格逐级回退，绝不因为一张图缺失让整页降级成文字：

    武器主视觉：武器图 → 角色立绘 → 武器占位图
    角色主视觉：角色立绘 → 角色占位图
    列表主视觉：通用档案占位图
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import image_codec
from .asset_manifest import (
    FALLBACK_ARCHIVE,
    FALLBACK_CHARACTER,
    FALLBACK_WEAPON,
    AssetManifest,
)

logger = logging.getLogger(__name__)

HERO_CHARACTER = "character"
HERO_WEAPON = "weapon"
HERO_ARCHIVE = "archive"
HERO_NONE = "none"

THUMB_EDGE = 160
FALLBACK_EDGE = 720


@dataclass(frozen=True)
class ResolvedArt:
    """一次解析的结果。hero 为空表示连占位图都没有，模板会退化成纯色卡片。"""

    hero: str = ""
    background: str = ""
    hero_kind: str = HERO_NONE
    hero_source: str = ""  # character / weapon / fallback:character …
    has_real_asset: bool = False


class AssetResolver:
    """素材解析器。所有失败都收敛成占位图，不向上抛。"""

    def __init__(
        self,
        manifest: AssetManifest | None = None,
        *,
        show_character_art: bool = True,
        show_weapon_art: bool = True,
    ):
        self.manifest = manifest
        self.show_character_art = show_character_art
        self.show_weapon_art = show_weapon_art
        self._fallback_cache: dict[str, str] = {}

    # ---- 基础 ----

    @property
    def root(self) -> Path:
        return self.manifest.root if self.manifest else Path(".")

    def _uri(self, relative: str, *, edge: int = image_codec.MAX_EDGE) -> str:
        if not relative:
            return ""
        return image_codec.encode_path(self.root / relative, edge=edge) or ""

    def placeholder(self, kind: str, *, edge: int = FALLBACK_EDGE) -> str:
        name = {
            HERO_CHARACTER: FALLBACK_CHARACTER,
            HERO_WEAPON: FALLBACK_WEAPON,
            HERO_ARCHIVE: FALLBACK_ARCHIVE,
        }.get(kind, FALLBACK_ARCHIVE)
        key = f"{name}:{edge}"
        if key not in self._fallback_cache:
            self._fallback_cache[key] = image_codec.encode_path(
                self.root / "fallback" / name, edge=edge
            ) or ""
        return self._fallback_cache[key]

    # ---- 角色 ----

    def _character_assets(self, keys: tuple[str, ...]):
        if not self.manifest:
            return None
        return self.manifest.character(*keys)

    def character_art(self, *keys: str) -> tuple[str, str, bool]:
        """返回 (立绘, 背景, 是否真实素材)。"""
        if not self.show_character_art:
            return "", "", False
        assets = self._character_assets(tuple(key for key in keys if key))
        if assets is None:
            return "", "", False
        portrait = self._uri(assets.portrait.relative_path)
        background = self._uri(assets.background.relative_path) or portrait
        return portrait, background, bool(portrait)

    def character_hero(self, *keys: str) -> ResolvedArt:
        if not self.show_character_art:
            # 明确关掉立绘时不要再退回占位图 —— 用户要的就是无图卡片
            return ResolvedArt()
        portrait, background, real = self.character_art(*keys)
        if portrait:
            return ResolvedArt(
                hero=portrait,
                background=background,
                hero_kind=HERO_CHARACTER,
                hero_source="character",
                has_real_asset=real,
            )
        return ResolvedArt(
            hero=self.placeholder(HERO_CHARACTER),
            background=self.placeholder(HERO_CHARACTER),
            hero_kind=HERO_CHARACTER,
            hero_source="fallback:character",
        )

    # ---- 武器 ----

    def weapon_image(self, owner_key: str, weapon_key: str, *, edge: int) -> str:
        if not (self.manifest and self.show_weapon_art and weapon_key):
            return ""
        entry = self.manifest.weapon(
            f"{owner_key}:{weapon_key}" if owner_key else "",
            weapon_key,
        )
        return self._uri(entry.relative_path, edge=edge)

    def weapon_thumb(self, owner_key: str, weapon_key: str) -> str:
        """条目左侧的缩略图。缺图时用武器占位图，保证排版不塌。"""
        if not self.show_weapon_art:
            return ""
        image = self.weapon_image(owner_key, weapon_key, edge=THUMB_EDGE)
        return image or self.placeholder(HERO_WEAPON, edge=THUMB_EDGE)

    def weapon_hero(self, owner_key: str, weapon_key: str) -> ResolvedArt:
        if not self.show_weapon_art:
            return ResolvedArt()
        image = self.weapon_image(owner_key, weapon_key, edge=image_codec.MAX_EDGE)
        if image:
            return ResolvedArt(
                hero=image,
                background=image,
                hero_kind=HERO_WEAPON,
                hero_source="weapon",
                has_real_asset=True,
            )
        # 武器缺图 → 退回角色立绘
        portrait, background, real = self.character_art(owner_key)
        if portrait:
            return ResolvedArt(
                hero=portrait,
                background=background or portrait,
                hero_kind=HERO_WEAPON,
                hero_source="character",
                has_real_asset=real,
            )
        return ResolvedArt(
            hero=self.placeholder(HERO_WEAPON),
            background=self.placeholder(HERO_WEAPON),
            hero_kind=HERO_WEAPON,
            hero_source="fallback:weapon",
        )

    # ---- 列表 ----

    def archive_hero(self) -> ResolvedArt:
        return ResolvedArt(
            hero=self.placeholder(HERO_ARCHIVE),
            background=self.placeholder(HERO_ARCHIVE),
            hero_kind=HERO_ARCHIVE,
            hero_source="fallback:archive",
        )


__all__ = [
    "AssetResolver",
    "ResolvedArt",
    "HERO_CHARACTER",
    "HERO_WEAPON",
    "HERO_ARCHIVE",
    "HERO_NONE",
    "THUMB_EDGE",
]
