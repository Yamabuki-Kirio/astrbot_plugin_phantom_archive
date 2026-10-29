"""把「谁在查」和「查的是谁」合并成模板要的一份视觉上下文。

模板只认这一份结构，不再自己去猜角色、拼路径、发请求。
这一层也是唯一把「页面」和「素材」放在一起看的地方 ——
因为武器页的主视觉是「当前页第一把武器」，这个信息只有分页之后才知道。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from . import asset_resolver, cards
from .asset_resolver import AssetResolver
from .identity import RequesterIdentity

logger = logging.getLogger(__name__)

DEFAULT_BACKGROUND_OPACITY = 0.24
DEFAULT_HERO_OPACITY = 0.92
DEFAULT_BACKGROUND_BLUR = 8

MIN_OPACITY = 0.0
MAX_OPACITY = 1.0
MAX_BLUR = 40


@dataclass(frozen=True)
class VisualSettings:
    show_requester_header: bool = True
    show_character_art: bool = True
    show_weapon_art: bool = True
    background_opacity: float = DEFAULT_BACKGROUND_OPACITY
    hero_opacity: float = DEFAULT_HERO_OPACITY
    background_blur: int = DEFAULT_BACKGROUND_BLUR

    @classmethod
    def from_config(cls, get) -> "VisualSettings":
        def raw(key, default):
            value = get(key, default)
            return default if value is None else value

        def as_bool(key, default):
            value = raw(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in ("true", "1", "yes", "on"):
                    return True
                if lowered in ("false", "0", "no", "off", ""):
                    return False
            return default

        def as_float(key, default, low, high):
            try:
                value = float(raw(key, default))
            except (TypeError, ValueError):
                logger.warning("配置 %s 非法（%r），回退默认 %s", key, raw(key, default), default)
                return default
            return min(high, max(low, value))

        def as_int(key, default, low, high):
            try:
                value = int(raw(key, default))
            except (TypeError, ValueError):
                logger.warning("配置 %s 非法（%r），回退默认 %s", key, raw(key, default), default)
                return default
            return min(high, max(low, value))

        return cls(
            show_requester_header=as_bool("show_requester_header", True),
            show_character_art=as_bool("show_character_art", True),
            show_weapon_art=as_bool("show_weapon_art", True),
            background_opacity=as_float(
                "card_background_opacity", DEFAULT_BACKGROUND_OPACITY, MIN_OPACITY, 0.6
            ),
            hero_opacity=as_float("card_hero_opacity", DEFAULT_HERO_OPACITY, 0.2, MAX_OPACITY),
            background_blur=as_int("card_background_blur", DEFAULT_BACKGROUND_BLUR, 0, MAX_BLUR),
        )


@dataclass(frozen=True)
class VisualContext:
    requester_name: str = ""
    requester_avatar: str = ""
    hero_image: str = ""
    background_image: str = ""
    hero_kind: str = asset_resolver.HERO_NONE
    has_requester_avatar: bool = False
    # 横构图立绘：模板改用 contain 贴底，避免 cover 把人物裁出画面
    hero_wide: bool = False
    # 立绘自带透明通道：不再叠 CSS 遮罩，否则会把人物左半身一起淡掉
    hero_transparent: bool = True
    has_hero_image: bool = False
    show_requester: bool = True
    background_opacity: float = DEFAULT_BACKGROUND_OPACITY
    hero_opacity: float = DEFAULT_HERO_OPACITY
    background_blur: int = DEFAULT_BACKGROUND_BLUR
    thumbs: Mapping[str, str] = field(default_factory=dict)

    def thumb_for(self, key: str) -> str:
        return self.thumbs.get(key, "") if key else ""

    def as_template_dict(self) -> dict:
        return {
            "show_requester": self.show_requester,
            "requester_name": self.requester_name,
            "requester_avatar": self.requester_avatar,
            "has_requester_avatar": self.has_requester_avatar,
            "hero_image": self.hero_image,
            "background_image": self.background_image,
            "hero_kind": self.hero_kind,
            "has_hero_image": self.has_hero_image,
            "hero_wide": self.hero_wide,
            "hero_transparent": self.hero_transparent,
            "background_opacity": round(self.background_opacity, 3),
            "hero_opacity": round(self.hero_opacity, 3),
            "background_blur": int(self.background_blur),
            "thumbs": dict(self.thumbs),
        }


EMPTY_VISUAL = VisualContext()


def _hero_for_page(page: cards.CardPage, resolver: AssetResolver):
    kind = page.hero_kind
    if kind == asset_resolver.HERO_WEAPON:
        return resolver.weapon_hero(page.owner_key, page.hero_key)
    if kind == asset_resolver.HERO_CHARACTER:
        return resolver.character_hero(
            page.hero_key, page.owner_key, variant=page.hero_variant
        )
    if kind == asset_resolver.HERO_ARCHIVE:
        return resolver.archive_hero()
    return None


def _collect_thumbs(page: cards.CardPage, resolver: AssetResolver) -> dict[str, str]:
    thumbs: dict[str, str] = {}
    for section in page.sections:
        for block in section.blocks:
            key = getattr(block, "ref_key", "")
            if not key or key in thumbs:
                continue
            image = resolver.weapon_thumb(page.owner_key, key)
            if image:
                thumbs[key] = image
    return thumbs


def build(
    page: cards.CardPage,
    *,
    identity: RequesterIdentity | None = None,
    resolver: AssetResolver | None = None,
    settings: VisualSettings | None = None,
) -> VisualContext:
    """合成一张卡片的视觉上下文。任何素材缺失都只影响视觉，不影响内容。"""
    settings = settings or VisualSettings()
    resolver = resolver or AssetResolver(None)

    name = ""
    avatar = ""
    if settings.show_requester_header and identity is not None:
        name = identity.display_name
        avatar = identity.avatar_data_uri

    hero = ""
    background = ""
    kind = asset_resolver.HERO_NONE
    try:
        resolved = _hero_for_page(page, resolver)
    except Exception:  # noqa: BLE001 - 素材出问题不能连累卡片
        logger.exception("解析主视觉失败，退回无图卡片")
        resolved = None

    if resolved is not None:
        hero = resolved.hero
        background = resolved.background or resolved.hero
        kind = resolved.hero_kind

    thumbs: dict[str, str] = {}
    if page.hero_kind == asset_resolver.HERO_WEAPON:
        try:
            thumbs = _collect_thumbs(page, resolver)
        except Exception:  # noqa: BLE001
            logger.exception("解析武器缩略图失败，本次不显示缩略图")
            thumbs = {}

    return VisualContext(
        requester_name=name,
        requester_avatar=avatar,
        hero_image=hero,
        background_image=background,
        hero_kind=kind,
        has_requester_avatar=bool(avatar),
        has_hero_image=bool(hero),
        hero_wide=bool(getattr(resolved, "hero_wide", False)) if resolved is not None else False,
        hero_transparent=(
            bool(getattr(resolved, "hero_transparent", True)) if resolved is not None else True
        ),
        show_requester=bool(settings.show_requester_header and identity is not None),
        background_opacity=settings.background_opacity,
        hero_opacity=settings.hero_opacity,
        background_blur=settings.background_blur,
        thumbs=thumbs,
    )


__all__ = [
    "VisualContext",
    "VisualSettings",
    "build",
    "EMPTY_VISUAL",
    "DEFAULT_BACKGROUND_OPACITY",
    "DEFAULT_HERO_OPACITY",
    "DEFAULT_BACKGROUND_BLUR",
]
