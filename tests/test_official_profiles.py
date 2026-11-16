import json
from pathlib import Path

import pytest

from archive.official_profiles import find_profile_images


DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.mark.parametrize("name", ["sp平野亚兰", "SP 平野亚兰", "平野亚兰sp", "sp亚兰", "亚兰 SP", "平野亚兰·凛华", "平野亞蘭·凜華"])
def test_special_form_images(name):
    images = find_profile_images(DATA, name)
    assert [p.name for p in images] == ["02.jpg"]
    assert all(p.read_bytes().startswith(b"\xff\xd8") for p in images)


@pytest.mark.parametrize("name", ["平野亚兰", "亚兰", "", "sp", "新井素羽"])
def test_other_characters_keep_database_route(name):
    assert find_profile_images(DATA, name) == ()


def test_missing_and_outside_images_fall_back(tmp_path):
    root = tmp_path / "official_profiles"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"image")
    for filename in ["missing.jpg", "../outside.jpg"]:
        (root / "index.json").write_text(json.dumps([{"name": "test", "images": [filename]}]))
        assert find_profile_images(tmp_path, "test") == ()

