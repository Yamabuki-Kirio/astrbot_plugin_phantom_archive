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
    image_presenter,
    image_renderer,
    image_templates,
    panel_history,
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
        awareness=4,
        weapon_mod=4,
    )
    review = panel_review.evaluate_panel(parsed, repo)
    text = panel_review.render_review_text(review)
    assert "无心象档" in text
    assert "4684" in text
    assert "5164" in text
    assert "5452" in text
    assert "15.93%" in text
    assert "全部达成" in text
    assert "4意识" in text
    assert "武器4改" in text
    prompt = panel_review.build_persona_llm_prompt(review)
    assert "汐见琴音" in prompt
    assert "4900" in prompt
    assert "4命（4意识）（高命座）" in prompt
    assert "4改" in prompt
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


def test_panel_history_store_tracks_improvements_same_regression_and_isolation(tmp_path):
    with pytest.raises(panel_history.PanelHistoryError):
        panel_history.PanelHistoryStore(tmp_path / "p5x_zh.sqlite3")

    store = panel_history.PanelHistoryStore(tmp_path / panel_history.PANEL_DB_FILENAME)
    repo = yituliou.load_repository()
    try:
        base_panel = panel_review.ParsedPanel(
            character_name="佐仓双叶",
            character_slug="navi",
            level=90,
            stats=(("生命值", "9000"), ("攻击力", "4800"), ("防御力", "1500")),
            skill_levels=(8, 8, 8, 1),
            revelation_set="【决心】&【职责】",
            weapon_name="HACK专业型",
            weapon_level=70,
            total_score=10500,
            anchor_hits=7,
            awareness=1,
            weapon_mod=0,
        )
        base_review = panel_review.evaluate_panel(base_panel, repo)

        # 1. 首次提交
        delta1 = store.record_and_compare(
            platform_id="qq",
            user_id="1001",
            display_name="十眼",
            parsed=base_panel,
            grade=base_review.grade,
        )
        assert delta1.status == "first"
        assert delta1.record_count == 1
        assert store.count() == 1

        # 2. 相同面板重复提交：判定无变化且不插入重复行
        delta_same = store.record_and_compare(
            platform_id="qq",
            user_id="1001",
            display_name="十眼",
            parsed=base_panel,
            grade=base_review.grade,
        )
        assert delta_same.status == "same"
        assert delta_same.record_count == 1
        assert store.count() == 1
        same_review = replace(base_review, progress=delta_same.as_dict())
        same_prompt = panel_review.build_persona_llm_prompt(same_review)
        assert "没有任何变化" in same_prompt
        same_text = panel_review.render_review_text(same_review)
        assert "对比上次" in same_text

        # 3. 练度明显提升：评分/意识1->3/武器改造0->4/攻击力/技能提升
        upgraded_panel = panel_review.ParsedPanel(
            character_name="佐仓双叶",
            character_slug="navi",
            level=100,
            stats=(("生命值", "11017"), ("攻击力", "7149"), ("防御力", "1927")),
            skill_levels=(13, 10, 13, 1),
            revelation_set="【决心】&【职责】",
            weapon_name="HACK专业型",
            weapon_level=80,
            total_score=13076,
            anchor_hits=8,
            awareness=3,
            weapon_mod=4,
        )
        upgraded_review = panel_review.evaluate_panel(upgraded_panel, repo)
        delta_up = store.record_and_compare(
            platform_id="qq",
            user_id="1001",
            display_name="十眼",
            parsed=upgraded_panel,
            grade=upgraded_review.grade,
        )
        assert delta_up.status == "improved"
        assert delta_up.record_count == 2
        assert store.count() == 2
        assert any("1命→3命" in item for item in delta_up.improvements)
        assert any("0改→4改" in item for item in delta_up.improvements)
        assert any("4800→7149" in item for item in delta_up.improvements)
        assert any("10500→13076" in item for item in delta_up.improvements)

        up_review = replace(upgraded_review, progress=delta_up.as_dict())
        up_text = panel_review.render_review_text(up_review)
        assert "📈 对比上次" in up_text
        assert "1命→3命" in up_text
        up_prompt = panel_review.build_persona_llm_prompt(up_review)
        assert "这次对比上次有明显提升" in up_prompt
        assert "0改→4改" in up_prompt

        # 校验带 progress 卡片渲染 & 动态增高画布
        page = image_presenter.panel_review_pages(up_review)[0]
        ctx = image_templates.build_context(page, theme="dark")
        assert ctx["canvas_height"] >= 920
        html = image_templates.render_page(page, theme="dark")
        assert "对比上次记录" in html
        assert "有提升" in html
        assert "1命→3命" in html

        # 4. 回调测试（评分与攻击力下降）
        regressed_panel = panel_review.ParsedPanel(
            character_name="佐仓双叶",
            character_slug="navi",
            level=100,
            stats=(("生命值", "9500"), ("攻击力", "4500"), ("防御力", "1600")),
            skill_levels=(13, 10, 13, 1),
            revelation_set="【决心】&【职责】",
            weapon_name="HACK专业型",
            weapon_level=80,
            total_score=9800,
            anchor_hits=8,
            awareness=3,
            weapon_mod=4,
        )
        regressed_review = panel_review.evaluate_panel(regressed_panel, repo)
        delta_down = store.record_and_compare(
            platform_id="qq",
            user_id="1001",
            display_name="十眼",
            parsed=regressed_panel,
            grade=regressed_review.grade,
        )
        assert delta_down.status == "regressed"
        assert delta_down.regressions
        assert store.count() == 3

        # 5. 不同用户 & 不同角色相互隔离
        delta_other_user = store.record_and_compare(
            platform_id="qq",
            user_id="2002",
            display_name="其他用户",
            parsed=upgraded_panel,
            grade=upgraded_review.grade,
        )
        assert delta_other_user.status == "first"

        kotone_panel = replace(
            upgraded_panel,
            character_name="汐见琴音",
            character_slug="kotone",
        )
        delta_other_char = store.record_and_compare(
            platform_id="qq",
            user_id="1001",
            display_name="十眼",
            parsed=kotone_panel,
            grade="A",
        )
        assert delta_other_char.status == "first"
    finally:
        store.close()


def test_progression_icon_templates_loaded():
    tmpls = panel_review._load_progression_templates()
    assert tmpls is not None
    assert len(tmpls["awareness"]) == 7
    assert len(tmpls["weapon"]) == 7
    assert tmpls["awareness_petal_mask"].ndim == 2
    assert int((tmpls["awareness_petal_mask"] > 0).sum()) > 100


@pytest.mark.parametrize("weapon_lvl", [0, 1, 2, 3, 4, 5, 6])
def test_detect_synthetic_weapon_mod_levels(weapon_lvl):
    from PIL import Image

    tmpls = panel_review._load_progression_templates()
    assert tmpls is not None

    w, h = 1024, 469
    canvas = Image.new("RGB", (w, h), (35, 28, 52))
    # 模拟武器卡紫色斜底与右下角改造六瓣环
    canvas.paste(Image.new("RGB", (140, 120), (88, 52, 138)), (310, 290))
    icon = Image.fromarray(tmpls["weapon"][weapon_lvl], mode="RGBA").resize(
        (38, 38), Image.Resampling.LANCZOS
    )
    canvas.paste(icon, (396, 336), icon)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    boxes = [
        panel_review.OcrBox("Lv.80", 0.99, 0.307, 0.770, 0.341, 0.800),
    ]
    awareness, weapon_mod = panel_review._detect_awareness_and_weapon_mod(buf.getvalue(), boxes)
    assert awareness is None
    assert weapon_mod == weapon_lvl


@pytest.mark.parametrize("aw_lvl", [0, 1, 2, 3, 4, 5, 6])
def test_detect_synthetic_awareness_levels_including_rainbow(aw_lvl):
    from PIL import Image

    tmpls = panel_review._load_progression_templates()
    assert tmpls is not None

    w, h = 1024, 469
    canvas = Image.new("RGB", (w, h), (18, 18, 22))
    # 模拟左上角角色铭牌浅灰紫渐变背景及右上角倾斜意识六芒星（+16°）
    canvas.paste(Image.new("RGB", (160, 62), (96, 92, 128)), (180, 52))
    icon = (
        Image.fromarray(tmpls["awareness"][aw_lvl], mode="RGBA")
        .rotate(-16.0, resample=Image.Resampling.BICUBIC, expand=False)
        .resize((55, 55), Image.Resampling.LANCZOS)
    )
    canvas.paste(icon, (262, 46), icon)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    boxes = [
        panel_review.OcrBox("LV.100", 0.99, 0.217, 0.188, 0.256, 0.228),
    ]
    awareness, weapon_mod = panel_review._detect_awareness_and_weapon_mod(buf.getvalue(), boxes)
    assert awareness == aw_lvl
    assert weapon_mod is None


def test_detect_progression_badges_returns_none_on_blank_roi():
    from PIL import Image

    w, h = 1024, 469
    canvas = Image.new("RGB", (w, h), (42, 36, 58))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    boxes = [
        panel_review.OcrBox("LV.100", 0.99, 0.217, 0.188, 0.256, 0.228),
        panel_review.OcrBox("Lv.80", 0.99, 0.307, 0.770, 0.341, 0.800),
    ]
    awareness, weapon_mod = panel_review._detect_awareness_and_weapon_mod(buf.getvalue(), boxes)
    assert awareness is None
    assert weapon_mod is None


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("149", 149),
        ("146", 146),
        ("131", 131),
        ("2138", 138),
    ],
)
def test_parse_rating_normal_and_mask_edge_prefixed(raw_text, expected):
    boxes = [
        panel_review.OcrBox("评级：", 0.98, 0.723, 0.748, 0.760, 0.802),
        panel_review.OcrBox(raw_text, 0.95, 0.797, 0.663, 0.881, 0.780),
        panel_review.OcrBox("综合评分：13076", 0.99, 0.777, 0.768, 0.888, 0.859),
    ]
    assert panel_review._parse_rating(boxes) == expected


def test_top_tier_rating_reflected_in_review_and_persona_prompt():
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
        total_score=14100,
        anchor_hits=8,
        awareness=6,
        weapon_mod=6,
        rating=149,
    )
    review = panel_review.evaluate_panel(parsed, repo)
    rating_items = [it for it in review.items if it.category == "游戏评级"]
    assert len(rating_items) == 1
    assert rating_items[0].status == "pass"
    assert "TOP 档" in rating_items[0].summary

    text = panel_review.render_review_text(review)
    assert "评级 149" in text
    assert "TOP 档" in text

    prompt = panel_review.build_persona_llm_prompt(review)
    assert "游戏内练度评级：149（游戏练度 TOP 档 / 顶级毕业）" in prompt
    assert "TOP 档" in prompt


def test_parse_weapon_snaps_garbled_ocr_and_rejects_noise():
    full_boxes = [
        panel_review.OcrBox("Lv.80", 0.99, 0.307, 0.770, 0.341, 0.800),
        panel_review.OcrBox("草！", 0.72, 0.350, 0.812, 0.387, 0.844),
    ]
    # 无可用已知专武对照时，带惊叹号噪点（「草！」）直接丢弃不输出乱码
    w_noise, lv_noise = panel_review._parse_weapon(full_boxes)
    assert w_noise == ""
    assert lv_noise == 80

    # 错别字形近误识对照组「月下领」 / 「天籁轻今」可自动吸附到一图流专武名
    crop_phoebe = [panel_review.OcrBox("月下领", 0.90, 0.344, 0.810, 0.391, 0.845)]
    w_ph, _ = panel_review._parse_weapon(
        full_boxes, weapon_crop_boxes=crop_phoebe, known_weapons=("月下翎", "天籁轻吟")
    )
    assert w_ph == "月下翎"

    crop_manaka = [panel_review.OcrBox("天籁轻今", 0.90, 0.344, 0.810, 0.391, 0.845)]
    w_mk, _ = panel_review._parse_weapon(
        full_boxes,
        weapon_crop_boxes=crop_manaka,
        known_weapons=("月下翎", "天籁轻吟"),
        preferred_weapon="天籁轻吟",
    )
    assert w_mk == "天籁轻吟"
