"""多张立绘：清单解析、按日期确定性挑选、缺图回退。

「一个角色多张图全部收下、生成时随机调用」这条要求，
落到代码上是「按日期键确定性地挑一张」——
不能用 random，否则同一天刷新一次就换一张图。
"""

from __future__ import annotations

import pytest

from archive import asset_manifest, asset_resolver

pytest.importorskip("PIL")

from PIL import Image as PILImage  # noqa: E402


def make_manifest(tmp_path, count: int = 3):
    root = tmp_path / "assets"
    folder = root / "characters" / "s1"
    folder.mkdir(parents=True)
    paths = []
    for index in range(count):
        target = folder / f"{index + 1}.webp"
        PILImage.new("RGB", (80, 100), (20 + index * 10, 20, 30)).save(
            target, format="WEBP"
        )
        paths.append(target.relative_to(root).as_posix())

    data = {
        "characters": {
            "s1": {
                "portrait": paths[0],
                "portraits": [{"path": path, "source_url": "https://example/a"} for path in paths],
                "background": paths[0],
            }
        }
    }
    return asset_manifest.parse_manifest(data, root)


def test_manifest_reads_all_portraits(tmp_path):
    manifest = make_manifest(tmp_path, count=3)
    assets = manifest.character("s1")
    assert len(assets.portraits) == 3
    assert len(assets.all_portraits) == 3
    assert manifest.counts()["portrait_files"] == 3


def test_manifest_falls_back_to_single_portrait():
    data = {"characters": {"s1": {"portrait": "characters/s1/1.webp"}}}
    manifest = asset_manifest.parse_manifest(data, ".")
    assets = manifest.character("s1")
    assert assets.portraits == ()
    assert len(assets.all_portraits) == 1


def test_manifest_dedupes_repeated_portraits():
    data = {
        "characters": {
            "s1": {
                "portraits": [
                    {"path": "characters/s1/1.webp"},
                    {"path": "characters/s1/1.webp"},
                ]
            }
        }
    }
    manifest = asset_manifest.parse_manifest(data, ".")
    assert len(manifest.character("s1").all_portraits) == 1


def test_manifest_skips_broken_portrait_items():
    data = {
        "characters": {
            "s1": {
                "portraits": [
                    "not-a-dict",
                    {"path": "/absolute/path.webp"},
                    {"path": "characters/s1/1.webp"},
                ]
            }
        }
    }
    manifest = asset_manifest.parse_manifest(data, ".")
    portraits = manifest.character("s1").all_portraits
    assert [entry.relative_path for entry in portraits] == ["characters/s1/1.webp"]


def test_pick_portrait_without_variant_uses_first(tmp_path):
    manifest = make_manifest(tmp_path, count=3)
    portraits = manifest.character("s1").all_portraits
    assert asset_resolver.AssetResolver.pick_portrait(portraits, "") == portraits[0]


def test_pick_portrait_is_stable_for_same_variant(tmp_path):
    manifest = make_manifest(tmp_path, count=3)
    portraits = manifest.character("s1").all_portraits
    chosen = {
        asset_resolver.AssetResolver.pick_portrait(portraits, "2026-10-27").relative_path
        for _ in range(20)
    }
    assert len(chosen) == 1


def test_pick_portrait_varies_across_variants(tmp_path):
    manifest = make_manifest(tmp_path, count=3)
    portraits = manifest.character("s1").all_portraits
    chosen = {
        asset_resolver.AssetResolver.pick_portrait(portraits, f"2026-11-{day:02d}").relative_path
        for day in range(1, 29)
    }
    assert len(chosen) > 1


def test_pick_portrait_single_item_is_always_itself(tmp_path):
    manifest = make_manifest(tmp_path, count=1)
    portraits = manifest.character("s1").all_portraits
    for day in range(5):
        chosen = asset_resolver.AssetResolver.pick_portrait(portraits, f"2026-11-{day + 1:02d}")
        assert chosen == portraits[0]


def test_pick_portrait_handles_empty_list():
    assert asset_resolver.AssetResolver.pick_portrait((), "2026-10-27").available is False


def test_character_hero_uses_variant(tmp_path):
    manifest = make_manifest(tmp_path, count=3)
    resolver = asset_resolver.AssetResolver(manifest)
    first = resolver.character_hero("s1", variant="2026-10-27")
    second = resolver.character_hero("s1", variant="2026-10-27")
    assert first.hero == second.hero
    assert first.has_real_asset is True
    assert first.hero.startswith("data:image/")


def test_character_hero_without_manifest_falls_back_to_placeholder(tmp_path):
    resolver = asset_resolver.AssetResolver(None)
    art = resolver.character_hero("s1", variant="2026-10-27")
    assert art.hero_kind == asset_resolver.HERO_CHARACTER
    assert art.hero_source == "fallback:character"
    assert art.has_real_asset is False


def test_show_character_art_off_returns_nothing(tmp_path):
    manifest = make_manifest(tmp_path, count=2)
    resolver = asset_resolver.AssetResolver(manifest, show_character_art=False)
    art = resolver.character_hero("s1", variant="2026-10-27")
    assert art.hero == ""
    assert art.hero_kind == asset_resolver.HERO_NONE
