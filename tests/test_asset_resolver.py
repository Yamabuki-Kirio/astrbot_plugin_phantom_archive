"""素材解析：逐级回退、缩略图、开关、缺文件不抛异常。"""

from __future__ import annotations

import base64
import io

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from archive import asset_manifest, asset_resolver, image_codec  # noqa: E402


def _write_image(path, color=(90, 30, 40), size=(300, 300)):
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="WEBP")
    path.write_bytes(buffer.getvalue())
    return path


def _decode(uri: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(uri.partition(",")[2])))


@pytest.fixture(autouse=True)
def _clear_codec_cache():
    image_codec.clear_cache()
    yield
    image_codec.clear_cache()


@pytest.fixture
def asset_root(tmp_path):
    """一棵最小素材树：一个角色有立绘、一个角色没有，两把武器。"""
    root = tmp_path / "assets"
    _write_image(root / "fallback" / "character.webp")
    _write_image(root / "fallback" / "weapon.webp")
    _write_image(root / "fallback" / "archive.webp")
    _write_image(root / "characters" / "kotone" / "portrait.webp", (10, 200, 90))
    _write_image(root / "characters" / "kotone" / "background.webp", (20, 30, 40))
    _write_image(root / "weapons" / "kotone" / "weapon5-1.webp", (240, 200, 40))
    manifest = asset_manifest.parse_manifest(
        {
            "characters": {
                "kotone": {
                    "portrait": "characters/kotone/portrait.webp",
                    "background": "characters/kotone/background.webp",
                    "source_status": "community",
                },
                "ryuji": {"portrait": None, "source_status": "unavailable"},
            },
            "weapons": {
                "kotone:weapon5-1": {
                    "image": "weapons/kotone/weapon5-1.webp",
                    "source_status": "community",
                },
                "kotone:weapon5-2": {"image": None, "source_status": "unavailable"},
            },
        },
        root,
    )
    return root, manifest


# ------------------------------------------------------------------ 角色


def test_character_hero_uses_real_portrait(asset_root):
    _, manifest = asset_root
    resolver = asset_resolver.AssetResolver(manifest)
    art = resolver.character_hero("kotone")
    assert art.hero.startswith("data:image/webp")
    assert art.hero_kind == asset_resolver.HERO_CHARACTER
    assert art.hero_source == "character"
    assert art.has_real_asset is True
    assert art.background  # 背景与立绘分开取


def test_character_hero_falls_back_to_placeholder(asset_root):
    _, manifest = asset_root
    resolver = asset_resolver.AssetResolver(manifest)
    art = resolver.character_hero("ryuji")
    assert art.hero.startswith("data:image/webp")
    assert art.hero_source == "fallback:character"
    assert art.has_real_asset is False


def test_character_hero_unknown_key_uses_placeholder(asset_root):
    _, manifest = asset_root
    art = asset_resolver.AssetResolver(manifest).character_hero("nobody")
    assert art.hero_source == "fallback:character"


def test_character_hero_tries_second_key(asset_root):
    _, manifest = asset_root
    art = asset_resolver.AssetResolver(manifest).character_hero("", "kotone")
    assert art.hero_source == "character"


def test_character_art_disabled_returns_nothing(asset_root):
    _, manifest = asset_root
    resolver = asset_resolver.AssetResolver(manifest, show_character_art=False)
    assert resolver.character_art("kotone") == ("", "", False)


# ------------------------------------------------------------------ 武器


def test_weapon_hero_uses_weapon_image(asset_root):
    _, manifest = asset_root
    art = asset_resolver.AssetResolver(manifest).weapon_hero("kotone", "weapon5-1")
    assert art.hero_kind == asset_resolver.HERO_WEAPON
    assert art.hero_source == "weapon"
    assert art.has_real_asset is True


def test_weapon_hero_falls_back_to_character_portrait(asset_root):
    _, manifest = asset_root
    art = asset_resolver.AssetResolver(manifest).weapon_hero("kotone", "weapon5-2")
    assert art.hero_source == "character"
    assert art.hero_kind == asset_resolver.HERO_WEAPON


def test_weapon_hero_falls_back_to_weapon_placeholder(asset_root):
    _, manifest = asset_root
    art = asset_resolver.AssetResolver(manifest).weapon_hero("ryuji", "weapon5-2")
    assert art.hero_source == "fallback:weapon"


def test_weapon_disabled_skips_real_image(asset_root):
    _, manifest = asset_root
    resolver = asset_resolver.AssetResolver(manifest, show_weapon_art=False)
    assert resolver.weapon_image("kotone", "weapon5-1", edge=200) == ""
    # 关掉武器图后既不回退到角色立绘、也不回退到占位图，直接无图
    assert resolver.weapon_hero("kotone", "weapon5-1").hero == ""
    assert resolver.weapon_thumb("kotone", "weapon5-1") == ""


def test_weapon_thumb_uses_placeholder_when_missing(asset_root):
    _, manifest = asset_root
    resolver = asset_resolver.AssetResolver(manifest)
    thumb = resolver.weapon_thumb("kotone", "weapon5-2")
    assert thumb.startswith("data:image/webp")
    assert max(_decode(thumb).size) <= asset_resolver.THUMB_EDGE


def test_weapon_thumb_is_small(asset_root):
    _, manifest = asset_root
    thumb = asset_resolver.AssetResolver(manifest).weapon_thumb("kotone", "weapon5-1")
    assert max(_decode(thumb).size) <= asset_resolver.THUMB_EDGE


# ------------------------------------------------------------------ 列表与空清单


def test_archive_hero_uses_placeholder(asset_root):
    _, manifest = asset_root
    art = asset_resolver.AssetResolver(manifest).archive_hero()
    assert art.hero_kind == asset_resolver.HERO_ARCHIVE
    assert art.hero_source == "fallback:archive"


def test_no_manifest_returns_placeholders(tmp_path):
    resolver = asset_resolver.AssetResolver(None)
    art = resolver.character_hero("kotone")
    # 没有清单目录时连占位图都读不到，只能返回空 —— 模板会退化成纯色卡片
    assert art.hero == "" or art.hero.startswith("data:image/webp")


def test_resolver_never_raises_on_broken_manifest(tmp_path):
    manifest = asset_manifest.parse_manifest(
        {"characters": {"x": {"portrait": "missing.webp"}}}, tmp_path
    )
    resolver = asset_resolver.AssetResolver(manifest)
    art = resolver.character_hero("x")
    assert art.hero_source == "fallback:character"


def test_placeholder_for_unknown_kind_is_archive(tmp_path):
    resolver = asset_resolver.AssetResolver(None)
    assert resolver.placeholder("something-else") == resolver.placeholder(
        asset_resolver.HERO_ARCHIVE
    )
