"""P5X 角色面板主动识图（纯本地 OCR）与一图流点评测试。"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest

from archive import (
    asset_manifest,
    asset_resolver,
    identity,
    image_renderer,
    image_templates,
    panel_review,
    service,
    visual_context,
    yituliou,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "p5x_zh.sqlite3"
MANIFEST_PATH = REPO_ROOT / "data" / "assets" / "manifest.json"


def _make_png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (30, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_prefilter_rejects_vertical_and_small_images():
    ok_small, _ = panel_review.passes_size_prefilter(_make_png(200, 120))
    assert not ok_small
    ok_vertical, _ = panel_review.passes_size_prefilter(_make_png(720, 1280))
    assert not ok_vertical
    ok_wide, size = panel_review.passes_size_prefilter(_make_png(1280, 720))
    assert ok_wide and size == (1280, 720)


def test_non_panel_ocr_boxes_return_none():
    repo = yituliou.load_repository()
    boxes = [
        panel_review.OcrBox("今天天气真不错", 0.99, 0.1, 0.1, 0.4, 0.15),
        panel_review.OcrBox("一起去吃饭吧", 0.98, 0.1, 0.2, 0.4, 0.25),
    ]
    assert panel_review.parse_panel_ocr(boxes, repo) is None


def test_parse_and_evaluate_graduated_futaba_panel():
    repo = yituliou.load_repository()
    # 模拟真实 OCR 输出（包含典型单字误识「佐爸双叶」）
    boxes = [
        panel_review.OcrBox("佐爸双叶", 0.70, 0.11, 0.14, 0.22, 0.21),
        panel_review.OcrBox("LV.100", 0.94, 0.22, 0.18, 0.27, 0.22),
        panel_review.OcrBox("属性", 1.00, 0.13, 0.28, 0.18, 0.31),
        panel_review.OcrBox("解明之力", 1.00, 0.29, 0.27, 0.37, 0.31),
        panel_review.OcrBox("生命值", 1.00, 0.14, 0.33, 0.20, 0.36),
        panel_review.OcrBox("11017", 0.99, 0.24, 0.33, 0.29, 0.36),
        panel_review.OcrBox("+攻击力", 0.97, 0.14, 0.36, 0.20, 0.39),
        panel_review.OcrBox("7149", 1.00, 0.24, 0.36, 0.29, 0.39),
        panel_review.OcrBox("+防御力", 0.91, 0.14, 0.40, 0.20, 0.43),
        panel_review.OcrBox("1927", 1.00, 0.24, 0.40, 0.29, 0.43),
        panel_review.OcrBox("核心属性目标", 0.98, 0.66, 0.40, 0.78, 0.44),
        panel_review.OcrBox("技能", 0.99, 0.13, 0.52, 0.18, 0.56),
        panel_review.OcrBox("Lv.13", 0.98, 0.17, 0.58, 0.22, 0.61),
        panel_review.OcrBox("Lv.13", 0.94, 0.25, 0.58, 0.30, 0.61),
        panel_review.OcrBox("Lv.13", 0.98, 0.17, 0.65, 0.22, 0.68),
        panel_review.OcrBox("Lv.1", 0.98, 0.25, 0.65, 0.30, 0.68),
        panel_review.OcrBox("Lv.80", 0.97, 0.31, 0.75, 0.36, 0.79),
        panel_review.OcrBox("【决心】&【职责】", 0.91, 0.16, 0.79, 0.29, 0.84),
        panel_review.OcrBox("HACK专业型", 1.00, 0.34, 0.79, 0.43, 0.84),
        panel_review.OcrBox("综合评分：13076", 0.98, 0.79, 0.75, 0.92, 0.80),
    ]
    parsed = panel_review.parse_panel_ocr(boxes, repo)
    assert parsed is not None
    assert parsed.character_name == "佐仓双叶"
    assert parsed.character_slug == "navi"
    assert parsed.level == 100
    assert parsed.stat_map()["攻击力"] == "7149"
    assert parsed.skill_levels == (13, 13, 13, 1)
    assert parsed.revelation_set == "【决心】&【职责】"
    assert parsed.weapon_name == "HACK专业型"
    assert parsed.weapon_level == 80
    assert parsed.total_score == 13076

    review = panel_review.evaluate_panel(parsed, repo)
    assert review.grade == "S"
    assert all(it.status == "pass" for it in review.items)
    text = panel_review.render_review_text(review)
    assert "佐仓双叶" in text
    assert "7149" in text
    assert "5060" in text


def test_evaluate_suboptimal_panel_flags_warnings():
    repo = yituliou.load_repository()
    parsed = panel_review.ParsedPanel(
        character_name="佐仓双叶",
        character_slug="navi",
        level=80,
        stats=(("生命值", "8000"), ("攻击力", "4200"), ("防御力", "1200")),
        skill_levels=(6, 4, 7, 1),
        revelation_set="【专注】&【爱】",
        weapon_name="四星过渡武器",
        weapon_level=70,
        total_score=8200,
        anchor_hits=6,
    )
    review = panel_review.evaluate_panel(parsed, repo)
    assert review.grade in ("B", "C")
    statuses = {it.category: it.status for it in review.items}
    assert statuses["启示卡套装"] == "warn"
    assert statuses["武器配置"] == "warn"
    assert statuses["核心属性·攻击力"] == "warn"
    assert statuses["技能加点"] == "warn"


def test_multitier_thresholds_and_core_targets():
    repo = yituliou.load_repository()
    parsed = panel_review.ParsedPanel(
        character_name="汐见琴音",
        character_slug="kotone",
        level=100,
        stats=(("生命值", "9800"), ("攻击力", "4900"), ("防御力", "1600")),
        skill_levels=(10, 8, 10, 8),
        revelation_set="【诞生】&【争执】",
        weapon_name="四星过渡武器",
        weapon_level=80,
        total_score=11800,
        anchor_hits=7,
        core_targets=(("攻击力", "4500"), ("生命值", "8000")),
    )
    review = panel_review.evaluate_panel(parsed, repo)
    text = panel_review.render_review_text(review)
    assert "无心象档" in text
    assert "4684" in text
    assert "5164" in text
    assert "5452" in text
    assert "15.93%" in text
    assert "全部达成" in text
    prompt = panel_review.build_persona_llm_prompt(review)
    assert "汐见琴音" in prompt
    assert "4900" in prompt
    assert "严格保持你当前扮演的人格" in prompt
    assert "严禁提及“OCR”" in prompt


@pytest.mark.parametrize("bg", ["phantom", "reload_blue", "phantom_red"])
def test_panel_review_card_renders_all_three_backgrounds(bg):
    repo = yituliou.load_repository()
    parsed = panel_review.ParsedPanel(
        character_name="佐仓双叶",
        character_slug="navi",
        level=100,
        stats=(("生命值", "11017"), ("攻击力", "7149"), ("防御力", "1927")),
        skill_levels=(13, 13, 13, 1),
        revelation_set="【决心】&【职责】",
        weapon_name="HACK专业型",
        weapon_level=80,
        total_score=13076,
        anchor_hits=8,
    )
    review = panel_review.evaluate_panel(parsed, repo)
    from archive import image_presenter

    page = image_presenter.panel_review_pages(review)[0]
    manifest = asset_manifest.load_manifest(MANIFEST_PATH)
    resolver = asset_resolver.AssetResolver(manifest)
    req = identity.RequesterIdentity(user_id="1", display_name="测试")
    vis = replace(visual_context.build(page, identity=req, resolver=resolver), background_preset=bg)
    html = image_templates.render_page(page, theme="dark", visual=vis)
    assert f'data-background="{bg}"' in html
    assert "佐仓双叶" in html
    assert "面板自动点评" in html
    settings = image_renderer.RenderSettings(endpoint="http://t2i.invalid/render")
    payload = image_renderer.build_payload(html, settings)
    assert "clip" in payload["options"]
