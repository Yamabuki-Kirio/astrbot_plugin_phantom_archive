"""一图流数据源、单图卡片渲染与附表查询测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from archive import (
    asset_manifest,
    asset_resolver,
    identity,
    image_renderer,
    image_templates,
    plain_commands,
    service,
    visual_context,
    yituliou,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "p5x_zh.sqlite3"
MANIFEST_PATH = REPO_ROOT / "data" / "assets" / "manifest.json"


@pytest.fixture(scope="module")
def svc():
    s = service.ArchiveService(DB_PATH)
    yield s
    s.close()


def test_yituliou_data_counts_and_slugs(svc):
    repo = yituliou.load_repository()
    assert len(repo.entries) == 50
    assert len(repo.tables) == 6

    db_slugs = {c.slug for c in svc.list_characters() if c.slug}
    manifest = asset_manifest.load_manifest(MANIFEST_PATH)

    for entry in repo.entries:
        assert entry.slug in db_slugs, f"{entry.name} slug={entry.slug} 不在数据库中"
        assert manifest.character(entry.slug) is not None, f"{entry.name} 缺少立绘素材"
        assert entry.name
        assert entry.author


@pytest.mark.parametrize(
    "query,expected_name",
    [
        ("sp平野亚兰", "平野亚兰·凛华"),
        ("汐见琴音", "汐见琴音"),
        ("哈姆子", "汐见琴音"),
        ("结城理", "结城理"),
        ("牢理", "结城理"),
        ("雨宫莲", "雨宫莲"),
        ("贼头", "雨宫莲"),
        ("初音未来", "初音未来"),
        ("摩尔加纳", "摩尔加纳"),
        ("双子反抗", "芮丝汀娜&卡萝莉娜"),
        ("双子救援", "芮丝汀娜&卡萝莉娜"),
        ("双子优越", "芮丝汀娜&卡萝莉娜"),
    ],
)
def test_character_outcome_returns_single_yituliou_card(svc, query, expected_name):
    outcome = svc.character_outcome(query)
    assert outcome.ok, f"{query} 查询失败: {outcome.text}"
    assert len(outcome.pages) == 1
    page = outcome.pages[0]
    assert page.category == "角色一图流"
    assert page.page_index == 1
    assert page.page_total == 1
    assert page.yituliou is not None
    assert page.yituliou.name == expected_name
    assert expected_name in outcome.text


def test_jc_without_mode_prompts_three_modes(svc):
    outcome = svc.character_outcome("双子")
    assert not outcome.ok
    assert outcome.error_kind == "ambiguous"
    assert "反抗" in outcome.text
    assert "救援" in outcome.text
    assert "优越" in outcome.text


def test_non_yituliou_character_falls_back_to_database(svc):
    outcome = svc.character_outcome("平野亚兰")
    assert outcome.ok
    assert outcome.pages[0].category == "角色"
    assert outcome.pages[0].yituliou is None


@pytest.mark.parametrize("bg", ["phantom", "reload_blue", "phantom_red"])
def test_yituliou_card_renders_all_three_backgrounds_and_clip(svc, bg):
    manifest = asset_manifest.load_manifest(MANIFEST_PATH)
    resolver = asset_resolver.AssetResolver(manifest)
    req = identity.RequesterIdentity(user_id="1", display_name="测试")
    settings = image_renderer.RenderSettings(endpoint="http://t2i.invalid/render")

    for query in ("sp平野亚兰", "汐见琴音", "摩尔加纳"):
        outcome = svc.character_outcome(query)
        page = outcome.pages[0]
        vis = replace(
            visual_context.build(page, identity=req, resolver=resolver),
            background_preset=bg,
        )
        html = image_templates.render_page(page, theme="dark", visual=vis)
        assert f'data-background="{bg}"' in html
        assert page.yituliou.name in html
        payload = image_renderer.build_payload(html, settings)
        assert "clip" in payload["options"]
        assert payload["options"]["clip"]["width"] >= 1480
        assert payload["options"]["clip"]["height"] >= 540


def test_all_tables_query_and_render(svc):
    listing = svc.table_outcome("")
    assert not listing.ok
    assert listing.error_kind == "usage"
    assert "1." in listing.text

    for idx in range(1, 7):
        outcome = svc.table_outcome(str(idx))
        assert outcome.ok
        assert len(outcome.pages) == 1
        page = outcome.pages[0]
        assert page.category == "数据附表"
        html = image_templates.render_page(page)
        assert page.yituliou_table.title in html


def test_plain_commands_include_tables():
    assert plain_commands.match_plain_command("p5x榜单") == "table_command"
    assert plain_commands.match_plain_command("p5x榜单 C倍率") == "table_command"
    assert plain_commands.match_plain_command("p5x附表 1") == "table_alias_command"
    assert plain_commands.match_plain_command("p5x倍率") == "dps_table_command"
    assert plain_commands.match_plain_command("p5x倍率表") == "dps_table_command"
    assert plain_commands.match_plain_command("p5x辅助榜") == "support_table_command"
    assert plain_commands.match_plain_command("p5x拐力榜") == "support_table_command"


@pytest.mark.parametrize("guide_type", ["review", "setting", "operation", "party"])
def test_guide_commands_prefer_yituliou_data(svc, guide_type):
    outcome = svc.guide_outcome("汐见琴音", guide_type)
    assert outcome.ok
    assert "强效召唤" in outcome.text
    assert outcome.has_pages
    # 意识/属性意识不覆盖，继续走原数据库
    ritual = svc.guide_outcome("sp平野亚兰", "ritual")
    assert ritual.ok
    assert "我所描绘的" in ritual.text


def test_yituliou_weapon_names_match_database_5star():
    import sqlite3

    repo = yituliou.load_repository()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        slug_to_key = dict(conn.execute("SELECT slug, source_key FROM zh_characters").fetchall())
        w5_by_key: dict[str, set[str]] = {}
        for ck, wname, rar in conn.execute(
            "SELECT character_key, name_zh, rarity FROM zh_weapons"
        ).fetchall():
            if rar == 5 and wname:
                w5_by_key.setdefault(ck, set()).add(wname)
    finally:
        conn.close()

    for entry in repo.entries:
        ck = slug_to_key.get(entry.slug)
        if not ck or ck not in w5_by_key:
            continue
        assert entry.weapon_name, f"{entry.name} ({entry.slug}) 缺少专武名"
        assert (
            entry.weapon_name in w5_by_key[ck]
        ), f"{entry.name} ({entry.slug}) 专武 {entry.weapon_name!r} 与数据库 5★ 武器 {sorted(w5_by_key[ck])} 不一致"
