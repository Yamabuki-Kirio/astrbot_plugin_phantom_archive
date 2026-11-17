from dataclasses import replace

from PIL import Image

from archive import cards, image_templates, visual_context
from archive.asset_manifest import parse_manifest
from archive.asset_resolver import AssetResolver


def test_pairing_switches_missing_and_weapon_fallback(tmp_path):
    for name in ("portrait", "persona", "weapon"):
        Image.new("RGBA", (80, 120), (50, 100, 150, 200)).save(tmp_path / f"{name}.webp")
    manifest = parse_manifest({"characters": {
        "paired": {"portrait": "portrait.webp", "persona": {"path": "persona.webp", "source_status": "community"}},
        "missing": {"portrait": "portrait.webp", "persona": {"path": "missing.webp"}},
        "unsafe": {"portrait": "portrait.webp", "persona": {"path": "../outside.webp"}},
    }, "weapons": {"paired:real": {"image": "weapon.webp"}}}, tmp_path)
    resolver = AssetResolver(manifest)
    card = cards.CardPage(category="角色", title="角色", hero_kind="character", hero_key="paired")
    context = visual_context.build(card, resolver=resolver)
    assert context.hero_image and context.persona_image
    for background in ("phantom", "reload_blue", "phantom_red"):
        html = image_templates.render_page(card, visual=replace(context, background_preset=background))
        assert 'class="persona-figure"' in html and "has-persona" in html
    for slug in ("missing", "unsafe", "unknown"):
        assert not visual_context.build(replace(card, hero_key=slug), resolver=resolver).persona_image
    off = visual_context.build(card, resolver=resolver, settings=visual_context.VisualSettings(show_persona_art=False))
    assert off.hero_image == context.hero_image and not off.persona_image
    assert not visual_context.build(card, resolver=AssetResolver(manifest, show_persona_art=False)).persona_image
    disabled = visual_context.build(card, resolver=AssetResolver(manifest, show_character_art=False))
    assert not disabled.hero_image and not disabled.persona_image
    weapon = replace(card, hero_kind="weapon", owner_key="paired", hero_key="real")
    assert not visual_context.build(weapon, resolver=resolver).persona_image
    assert visual_context.build(replace(weapon, hero_key="missing"), resolver=resolver).persona_image


def test_config_boolean_and_default():
    assert visual_context.VisualSettings.from_config(lambda key, default: default).show_persona_art
    assert not visual_context.VisualSettings.from_config(lambda key, default: "false" if key == "show_persona_art" else default).show_persona_art
