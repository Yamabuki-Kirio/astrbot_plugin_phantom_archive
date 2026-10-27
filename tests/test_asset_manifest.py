"""素材清单：路径安全、来源状态白名单、缺文件不报错、覆盖率统计。"""

from __future__ import annotations

import json

from archive import asset_manifest


def _manifest_dict() -> dict:
    return {
        "characters": {
            "kotone": {
                "portrait": "characters/kotone/portrait.webp",
                "background": "characters/kotone/bg.webp",
                "source_url": "https://example.invalid/kotone",
                "source_status": "community",
            },
            "ryuji": {"portrait": "characters/ryuji/portrait.webp"},
        },
        "weapons": {
            "kotone:weapon5-1": {
                "image": "weapons/kotone/weapon5-1.webp",
                "source_url": "https://example.invalid/w1",
                "source_status": "community",
            }
        },
    }


def test_parse_reads_characters_and_weapons(tmp_path):
    manifest = asset_manifest.parse_manifest(_manifest_dict(), tmp_path)
    assert manifest.loaded
    assert set(manifest.characters) == {"kotone", "ryuji"}
    assert manifest.character("kotone").portrait.relative_path.endswith("portrait.webp")
    assert manifest.character("kotone").background.relative_path.endswith("bg.webp")
    assert manifest.weapon("kotone:weapon5-1").relative_path.endswith("weapon5-1.webp")


def test_counts_report_real_numbers(tmp_path):
    counts = asset_manifest.parse_manifest(_manifest_dict(), tmp_path).counts()
    assert counts["characters"] == 2
    assert counts["characters_with_portrait"] == 2
    assert counts["weapons"] == 1
    assert counts["weapons_with_image"] == 1


def test_missing_key_returns_empty_entry(tmp_path):
    manifest = asset_manifest.parse_manifest(_manifest_dict(), tmp_path)
    assert manifest.character("nobody").portrait.available is False
    assert manifest.weapon("nobody:weapon").available is False


def test_lookup_falls_back_through_keys(tmp_path):
    manifest = asset_manifest.parse_manifest(_manifest_dict(), tmp_path)
    assert manifest.character("", "kotone").portrait.available
    assert manifest.weapon("", "kotone:weapon5-1").available


# ------------------------------------------------------------------ 路径安全


def test_absolute_path_is_rejected(tmp_path):
    data = {"characters": {"x": {"portrait": "C:/Windows/win.ini"}}}
    manifest = asset_manifest.parse_manifest(data, tmp_path)
    assert manifest.character("x").portrait.relative_path == ""


def test_parent_traversal_is_rejected(tmp_path):
    data = {"characters": {"x": {"portrait": "../../../etc/passwd"}}}
    manifest = asset_manifest.parse_manifest(data, tmp_path)
    assert manifest.character("x").portrait.relative_path == ""


def test_normal_relative_path_is_kept(tmp_path):
    data = {"characters": {"x": {"portrait": "characters/x/portrait.webp"}}}
    manifest = asset_manifest.parse_manifest(data, tmp_path)
    assert manifest.character("x").portrait.relative_path == "characters/x/portrait.webp"


# ------------------------------------------------------------------ 来源状态


def test_unknown_source_status_becomes_unavailable(tmp_path):
    data = {"characters": {"x": {"portrait": "a.webp", "source_status": "官方"}}}
    manifest = asset_manifest.parse_manifest(data, tmp_path)
    assert manifest.character("x").portrait.source_status == "unavailable"


def test_missing_source_status_is_not_official(tmp_path):
    data = {"characters": {"x": {"portrait": "a.webp"}}}
    manifest = asset_manifest.parse_manifest(data, tmp_path)
    assert manifest.character("x").portrait.source_status == "unavailable"


def test_official_status_is_preserved_when_declared(tmp_path):
    data = {"characters": {"x": {"portrait": "a.webp", "source_status": "official"}}}
    manifest = asset_manifest.parse_manifest(data, tmp_path)
    assert manifest.character("x").portrait.source_status == "official"


def test_source_url_is_kept(tmp_path):
    manifest = asset_manifest.parse_manifest(_manifest_dict(), tmp_path)
    assert manifest.character("kotone").portrait.source_url == "https://example.invalid/kotone"


# ------------------------------------------------------------------ 加载


def test_load_missing_file_returns_empty(tmp_path):
    manifest = asset_manifest.load_manifest(tmp_path / "nope.json")
    assert manifest.loaded is False
    assert manifest.root == tmp_path


def test_load_broken_json_returns_empty(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    assert asset_manifest.load_manifest(path).loaded is False


def test_load_non_object_returns_empty(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert asset_manifest.load_manifest(path).loaded is False


def test_load_real_file(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest_dict()), encoding="utf-8")
    manifest = asset_manifest.load_manifest(path)
    assert manifest.loaded
    assert manifest.root == tmp_path


def test_load_none_returns_empty():
    assert asset_manifest.load_manifest(None).loaded is False


def test_empty_manifest_counts_are_zero():
    counts = asset_manifest.EMPTY_MANIFEST.counts()
    assert counts == {
        "characters": 0,
        "characters_with_portrait": 0,
        "weapons": 0,
        "weapons_with_image": 0,
    }
