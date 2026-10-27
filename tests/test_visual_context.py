"""视觉上下文：表头开关、主视觉来源、缩略图收集、配置夹取。"""

from __future__ import annotations

import base64
import io

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from archive import asset_manifest, asset_resolver, cards, image_codec, visual_context  # noqa: E402
from archive.identity import RequesterIdentity  # noqa: E402

AVATAR = "data:image/webp;base64,AAAA"


def _write_image(path, color=(90, 30, 40), size=(300, 300)):
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="WEBP")
    path.write_bytes(buffer.getvalue())


@pytest.fixture(autouse=True)
def _clear_codec_cache():
    image_codec.clear_cache()
    yield
    image_codec.clear_cache()


@pytest.fixture
def resolver(tmp_path):
    root = tmp_path / "assets"
    for name in ("character", "weapon", "archive"):
        _write_image(root / "fallback" / f"{name}.webp")
    _write_image(root / "characters" / "kotone" / "portrait.webp")
    _write_image(root / "weapons" / "kotone" / "weapon5-1.webp")
    manifest = asset_manifest.parse_manifest(
        {
            "characters": {"kotone": {"portrait": "characters/kotone/portrait.webp"}},
            "weapons": {"kotone:weapon5-1": {"image": "weapons/kotone/weapon5-1.webp"}},
        },
        root,
    )
    return asset_resolver.AssetResolver(manifest)


def _page(*, hero_kind="", hero_key="", owner_key="", sections=()) -> cards.CardPage:
    return cards.CardPage(
        category="角色",
        title="汐见琴音",
        subtitle="",
        lead="",
        sections=sections,
        page_index=1,
        page_total=1,
        source_label="",
        translation_label="",
        has_conflict=False,
        show_footer=True,
        hero_kind=hero_kind,
        hero_key=hero_key,
        owner_key=owner_key,
    )


def _identity(name="山吹桐生", avatar=AVATAR) -> RequesterIdentity:
    return RequesterIdentity("10001", name, avatar)


# ------------------------------------------------------------------ 表头


def test_requester_is_passed_through(resolver):
    ctx = visual_context.build(_page(hero_kind="character", hero_key="kotone"),
                               identity=_identity(), resolver=resolver)
    assert ctx.show_requester is True
    assert ctx.requester_name == "山吹桐生"
    assert ctx.requester_avatar == AVATAR
    assert ctx.has_requester_avatar is True


def test_requester_hidden_by_config(resolver):
    settings = visual_context.VisualSettings(show_requester_header=False)
    ctx = visual_context.build(_page(), identity=_identity(), resolver=resolver, settings=settings)
    assert ctx.show_requester is False
    assert ctx.requester_name == ""
    assert ctx.requester_avatar == ""


def test_no_identity_means_no_header(resolver):
    ctx = visual_context.build(_page(), resolver=resolver)
    assert ctx.show_requester is False
    assert ctx.requester_name == ""


def test_identity_without_avatar_keeps_name(resolver):
    ctx = visual_context.build(_page(), identity=_identity(avatar=""), resolver=resolver)
    assert ctx.show_requester is True
    assert ctx.has_requester_avatar is False


# ------------------------------------------------------------------ 主视觉


def test_character_page_gets_portrait(resolver):
    ctx = visual_context.build(_page(hero_kind="character", hero_key="kotone"),
                               identity=_identity(), resolver=resolver)
    assert ctx.hero_kind == asset_resolver.HERO_CHARACTER
    assert ctx.has_hero_image is True
    assert ctx.hero_image.startswith("data:image/webp")
    assert ctx.background_image.startswith("data:image/webp")


def test_weapon_page_gets_weapon_image(resolver):
    ctx = visual_context.build(_page(hero_kind="weapon", hero_key="weapon5-1", owner_key="kotone"),
                               resolver=resolver)
    assert ctx.hero_kind == asset_resolver.HERO_WEAPON
    assert ctx.has_hero_image is True


def test_archive_page_gets_archive_placeholder(resolver):
    ctx = visual_context.build(_page(hero_kind="archive"), resolver=resolver)
    assert ctx.hero_kind == asset_resolver.HERO_ARCHIVE
    assert ctx.has_hero_image is True


def test_page_without_hero_kind_has_no_image(resolver):
    ctx = visual_context.build(_page(), resolver=resolver)
    assert ctx.hero_kind == asset_resolver.HERO_NONE
    assert ctx.has_hero_image is False
    assert ctx.hero_image == ""


def test_unknown_hero_kind_does_not_raise(resolver):
    ctx = visual_context.build(_page(hero_kind="mystery", hero_key="x"), resolver=resolver)
    assert ctx.hero_kind == asset_resolver.HERO_NONE


def test_thumbnails_collected_for_weapon_pages(resolver):
    sections = (
        cards.Section(
            heading="武器",
            blocks=(
                cards.Block(kind="item", lines=("· A", "x"), ref_key="weapon5-1"),
                cards.Block(kind="item", lines=("· B", "y"), ref_key="weapon5-2"),
            ),
        ),
    )
    page = _page(hero_kind="weapon", hero_key="weapon5-1", owner_key="kotone", sections=sections)
    ctx = visual_context.build(page, resolver=resolver)
    assert set(ctx.thumbs) == {"weapon5-1", "weapon5-2"}
    assert ctx.thumb_for("weapon5-1").startswith("data:image/webp")
    assert ctx.thumb_for("weapon5-2").startswith("data:image/webp")
    assert ctx.thumb_for("") == ""
    assert ctx.thumb_for("nope") == ""


def test_character_pages_have_no_thumbnails(resolver):
    sections = (
        cards.Section(
            heading="技能",
            blocks=(cards.Block(kind="item", lines=("· A", "x"), ref_key="skill1"),),
        ),
    )
    page = _page(hero_kind="character", hero_key="kotone", sections=sections)
    assert visual_context.build(page, resolver=resolver).thumbs == {}


# ------------------------------------------------------------------ 配置


def test_settings_from_config_defaults():
    settings = visual_context.VisualSettings.from_config(lambda key, default: default)
    assert settings.show_requester_header is True
    assert settings.background_opacity == visual_context.DEFAULT_BACKGROUND_OPACITY
    assert settings.hero_opacity == visual_context.DEFAULT_HERO_OPACITY
    assert settings.background_blur == visual_context.DEFAULT_BACKGROUND_BLUR


def test_settings_clamp_opacity_and_blur():
    values = {
        "card_background_opacity": 5.0,
        "card_hero_opacity": -1,
        "card_background_blur": 999,
    }
    settings = visual_context.VisualSettings.from_config(
        lambda key, default: values.get(key, default)
    )
    assert settings.background_opacity == 0.6
    assert settings.hero_opacity == 0.2
    assert settings.background_blur == visual_context.MAX_BLUR


def test_settings_invalid_value_falls_back():
    values = {"card_background_opacity": "not-a-number", "card_background_blur": ""}
    settings = visual_context.VisualSettings.from_config(
        lambda key, default: values.get(key, default)
    )
    assert settings.background_opacity == visual_context.DEFAULT_BACKGROUND_OPACITY
    assert settings.background_blur == visual_context.DEFAULT_BACKGROUND_BLUR


def test_settings_parse_string_bools():
    values = {"show_requester_header": "false", "show_character_art": "on"}
    settings = visual_context.VisualSettings.from_config(
        lambda key, default: values.get(key, default)
    )
    assert settings.show_requester_header is False
    assert settings.show_character_art is True


def test_template_dict_is_plain_data(resolver):
    ctx = visual_context.build(_page(hero_kind="character", hero_key="kotone"),
                               identity=_identity(), resolver=resolver)
    data = ctx.as_template_dict()
    assert isinstance(data, dict)
    assert data["requester_name"] == "山吹桐生"
    assert data["hero_kind"] == asset_resolver.HERO_CHARACTER
    assert isinstance(data["thumbs"], dict)
    assert 0 <= data["background_opacity"] <= 1
    assert isinstance(data["background_blur"], int)


def test_empty_visual_is_neutral():
    data = visual_context.EMPTY_VISUAL.as_template_dict()
    assert data["show_requester"] is True
    assert data["hero_image"] == ""
    assert data["has_hero_image"] is False


def test_broken_resolver_does_not_break_card():
    class Broken:
        def character_hero(self, *keys):
            raise RuntimeError("boom")

        def weapon_hero(self, *keys):
            raise RuntimeError("boom")

        def archive_hero(self):
            raise RuntimeError("boom")

    ctx = visual_context.build(_page(hero_kind="character", hero_key="kotone"), resolver=Broken())
    assert ctx.hero_image == ""
    assert ctx.hero_kind == asset_resolver.HERO_NONE
