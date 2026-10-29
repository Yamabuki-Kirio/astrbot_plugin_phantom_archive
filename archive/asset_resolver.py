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

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from . import image_codec
from .asset_manifest import (
    FALLBACK_ARCHIVE,
    FALLBACK_CHARACTER,
    FALLBACK_WEAPON,
    AssetEntry,
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
    # 横构图（同一角色的动作大字图）：模板要用 contain 贴底，cover 会把人挤出去
    hero_wide: bool = False
    # 立绘自带透明通道时不需要 CSS 遮罩融边，加了反而会把人物淡掉
    hero_transparent: bool = True


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

    def _wide(self, relative: str) -> bool:
        """这张素材是不是横构图（决定模板用 cover 还是 contain）。"""
        return bool(relative) and image_codec.is_wide_image(self.root / relative)

    def _transparent(self, relative: str) -> bool:
        """素材是否自带透明通道（决定模板要不要再加 CSS 遮罩融边）。"""
        return bool(relative) and image_codec.has_transparency(self.root / relative)

    def _fallback_relative(self, kind: str) -> str:
        name = {
            HERO_CHARACTER: FALLBACK_CHARACTER,
            HERO_WEAPON: FALLBACK_WEAPON,
            HERO_ARCHIVE: FALLBACK_ARCHIVE,
        }.get(kind, FALLBACK_ARCHIVE)
        return f"fallback/{name}"

    def placeholder(self, kind: str, *, edge: int = FALLBACK_EDGE) -> str:
        relative = self._fallback_relative(kind)
        key = f"{relative}:{edge}"
        if key not in self._fallback_cache:
            self._fallback_cache[key] = image_codec.encode_path(
                self.root / relative, edge=edge
            ) or ""
        return self._fallback_cache[key]

    # ---- 角色 ----

    def _character_assets(self, keys: tuple[str, ...]):
        if not self.manifest:
            return None
        return self.manifest.character(*keys)

    @staticmethod
    def pick_portrait(portraits: tuple[AssetEntry, ...], variant: str) -> AssetEntry:
        """从多张立绘里挑一张。

        ``variant`` 为空 → 第一张（查询卡片的行为保持不变）。
        ``variant`` 非空 → 按 ``SHA256(第一张路径|variant)`` 取模，
        同一 variant 永远同一张：每日运势传日期键，所以同一天不会换图，
        换一天才换一张。这里刻意不用 ``random``，否则重启就会变。
        """
        if not portraits:
            return AssetEntry()
        if not variant or len(portraits) == 1:
            return portraits[0]
        raw = f"{portraits[0].relative_path}|{variant}".encode("utf-8")
        index = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % len(portraits)
        return portraits[index]

    def character_art(self, *keys: str, variant: str = "") -> tuple[str, str, bool]:
        """返回 (立绘, 背景, 是否真实素材)。"""
        if not self.show_character_art:
            return "", "", False
        assets = self._character_assets(tuple(key for key in keys if key))
        if assets is None:
            return "", "", False
        chosen = self.pick_portrait(assets.all_portraits, variant)
        portrait = self._uri(chosen.relative_path)
        background = self._uri(assets.background.relative_path) or portrait
        return portrait, background, bool(portrait)

    def _portrait_entry(self, keys: tuple[str, ...], variant: str):
        assets = self._character_assets(tuple(key for key in keys if key))
        if assets is None:
            return None
        return self.pick_portrait(assets.all_portraits, variant)

    def character_hero(self, *keys: str, variant: str = "") -> ResolvedArt:
        if not self.show_character_art:
            # 明确关掉立绘时不要再退回占位图 —— 用户要的就是无图卡片
            return ResolvedArt()
        portrait, background, real = self.character_art(*keys, variant=variant)
        if portrait:
            entry = self._portrait_entry(keys, variant)
            return ResolvedArt(
                hero=portrait,
                background=background,
                hero_kind=HERO_CHARACTER,
                hero_source="character",
                has_real_asset=real,
                hero_wide=self._wide(entry.relative_path) if entry else False,
                hero_transparent=self._transparent(entry.relative_path) if entry else False,
            )
        return ResolvedArt(
            hero=self.placeholder(HERO_CHARACTER),
            background=self.placeholder(HERO_CHARACTER),
            hero_kind=HERO_CHARACTER,
            hero_source="fallback:character",
            hero_wide=self._wide(self._fallback_relative(HERO_CHARACTER)),
            hero_transparent=self._transparent(self._fallback_relative(HERO_CHARACTER)),
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

    def _weapon_entry(self, owner_key: str, weapon_key: str):
        if not (self.manifest and weapon_key):
            return None
        return self.manifest.weapon(
            f"{owner_key}:{weapon_key}" if owner_key else "",
            weapon_key,
        )

    def weapon_hero(self, owner_key: str, weapon_key: str) -> ResolvedArt:
        if not self.show_weapon_art:
            return ResolvedArt()
        image = self.weapon_image(owner_key, weapon_key, edge=image_codec.MAX_EDGE)
        if image:
            entry = self._weapon_entry(owner_key, weapon_key)
            return ResolvedArt(
                hero=image,
                background=image,
                hero_kind=HERO_WEAPON,
                hero_source="weapon",
                has_real_asset=True,
                hero_wide=self._wide(entry.relative_path) if entry else False,
                hero_transparent=self._transparent(entry.relative_path) if entry else False,
            )
        # 武器缺图 → 退回角色立绘
        portrait, background, real = self.character_art(owner_key)
        if portrait:
            entry = self._portrait_entry((owner_key,), "")
            return ResolvedArt(
                hero=portrait,
                background=background or portrait,
                hero_kind=HERO_WEAPON,
                hero_source="character",
                has_real_asset=real,
                hero_wide=self._wide(entry.relative_path) if entry else False,
                hero_transparent=self._transparent(entry.relative_path) if entry else False,
            )
        return ResolvedArt(
            hero=self.placeholder(HERO_WEAPON),
            background=self.placeholder(HERO_WEAPON),
            hero_kind=HERO_WEAPON,
            hero_source="fallback:weapon",
            hero_wide=self._wide(self._fallback_relative(HERO_WEAPON)),
            hero_transparent=self._transparent(self._fallback_relative(HERO_WEAPON)),
        )

    # ---- 列表 ----

    def archive_hero(self) -> ResolvedArt:
        return ResolvedArt(
            hero=self.placeholder(HERO_ARCHIVE),
            background=self.placeholder(HERO_ARCHIVE),
            hero_kind=HERO_ARCHIVE,
            hero_source="fallback:archive",
            hero_wide=self._wide(self._fallback_relative(HERO_ARCHIVE)),
            hero_transparent=self._transparent(self._fallback_relative(HERO_ARCHIVE)),
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
