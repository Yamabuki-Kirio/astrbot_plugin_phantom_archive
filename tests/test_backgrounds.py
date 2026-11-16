import json
from dataclasses import replace
from pathlib import Path

import pytest

from archive.backgrounds import BACKGROUND_FILES, BACKGROUND_SIZES, BackgroundRotation
from archive import cards, image_templates, visual_context


def test_rotation_and_fixed_choice():
    selector = BackgroundRotation()
    assert [selector.select() for _ in range(6)] == list(BACKGROUND_FILES) * 2
    assert selector.select(rotate=False, fixed="reload_blue") == "reload_blue"
    assert selector.select(rotate=False, fixed="phantom_red") == "phantom_red"
    assert selector.select(rotate=False, fixed="invalid") == "phantom"
    assert selector.select() == "phantom"


@pytest.mark.parametrize("preset", BACKGROUND_FILES)
@pytest.mark.parametrize("category", ["角色", "每日运势", "技能", "武器", "角色列表", "攻略"])
def test_each_category_embeds_selected_background(preset, category):
    page = cards.CardPage(category=category, title="测试")
    visual = replace(visual_context.EMPTY_VISUAL, background_preset=preset)
    html = image_templates.render_page(page, visual=visual)
    assert f'data-background="{preset}"' in html
    assert image_templates.card_background_uri(preset) in html
    assert image_templates.card_background_uri(preset).startswith("data:image/png;base64,")
    assert '.hero-bg, .hero-scrim, .hero-top { display: none; }' in html
    width, height = BACKGROUND_SIZES[preset]
    assert f'--canvas-width:{width}px;--canvas-height:{height}px;' in html
    from archive.image_renderer import build_payload, RenderSettings
    options = build_payload(html, RenderSettings())["options"]
    assert (options["viewport_width"], options["viewport_height"]) == (width, height)
    assert options["full_page"] is False


def test_canvas_sizes_match_background_pixels():
    from PIL import Image
    root = Path(__file__).resolve().parents[1] / 'data/assets/backgrounds'
    for preset, filename in BACKGROUND_FILES.items():
        with Image.open(root / filename) as image:
            assert image.size == BACKGROUND_SIZES[preset]


def test_configuration_defaults_to_rotation():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["card_background_rotation"]["default"] is True
    assert schema["card_background"]["options"] == list(BACKGROUND_FILES)


def test_random_selection_and_fixed_override(monkeypatch):
    import archive.backgrounds as backgrounds
    calls = []
    def choose(values):
        calls.append(values)
        return "phantom_red"
    monkeypatch.setattr(backgrounds, "choice", choose)
    selector = BackgroundRotation()
    assert selector.select(mode="random") == "phantom_red"
    assert calls == [tuple(BACKGROUND_FILES)]
    assert selector.select(rotate=False, mode="random", fixed="reload_blue") == "reload_blue"
    assert len(calls) == 1
    assert selector.select() == "phantom"
