"""Data URI 编码器：格式嗅探、体积上限、缩放、缓存、首字符头像。"""

from __future__ import annotations

import base64
import io

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from archive import image_codec  # noqa: E402


def _image(fmt: str, size: tuple[int, int] = (400, 300), color=(120, 40, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _decode(uri: str) -> Image.Image:
    header, _, payload = uri.partition(",")
    assert header.startswith("data:image/")
    assert "base64" in header
    return Image.open(io.BytesIO(base64.b64decode(payload)))


@pytest.fixture(autouse=True)
def _clear_cache():
    image_codec.clear_cache()
    yield
    image_codec.clear_cache()


# ------------------------------------------------------------------ 嗅探


@pytest.mark.parametrize(
    "fmt,expected",
    [("PNG", "PNG"), ("JPEG", "JPEG"), ("WEBP", "WEBP")],
)
def test_sniff_format(fmt, expected):
    assert image_codec.sniff_format(_image(fmt)) == expected


def test_sniff_rejects_garbage():
    assert image_codec.sniff_format(b"") is None
    assert image_codec.sniff_format(b"GIF89a") is None
    assert image_codec.sniff_format(b"<html>") is None


def test_is_supported_matches_sniff():
    assert image_codec.is_supported(_image("PNG"))
    assert not image_codec.is_supported(b"BM....")


# ------------------------------------------------------------------ 编码


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_encode_bytes_produces_webp_data_uri(fmt):
    uri = image_codec.encode_bytes(_image(fmt), edge=200)
    assert uri is not None
    image = _decode(uri)
    assert image.format == "WEBP"
    assert max(image.size) <= 200


def test_encode_bytes_keeps_aspect_ratio():
    uri = image_codec.encode_bytes(_image("PNG", (400, 200)), edge=100)
    image = _decode(uri)
    assert image.size == (100, 50)


def test_encode_bytes_does_not_upscale():
    uri = image_codec.encode_bytes(_image("PNG", (40, 30)), edge=200)
    assert _decode(uri).size == (40, 30)


def test_encode_bytes_rejects_unsupported_format():
    assert image_codec.encode_bytes(b"not an image") is None


def test_encode_bytes_rejects_oversized_source():
    huge = b"\x89PNG\r\n\x1a\n" + b"0" * (image_codec.MAX_SOURCE_BYTES + 1)
    assert image_codec.encode_bytes(huge) is None


def test_encode_bytes_rejects_oversized_output():
    # 上限压到 1 字节，任何真实图片都装不下，必须返回 None 而不是超限
    assert image_codec.encode_bytes(_image("PNG"), max_bytes=1) is None


# ------------------------------------------------------------------ 路径


def test_encode_path_reads_file(tmp_path):
    path = tmp_path / "art.png"
    path.write_bytes(_image("PNG"))
    uri = image_codec.encode_path(path, edge=120)
    assert uri is not None
    assert max(_decode(uri).size) <= 120


def test_encode_path_missing_file(tmp_path):
    assert image_codec.encode_path(tmp_path / "nope.webp") is None


def test_encode_path_rejects_oversized_file(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (image_codec.MAX_SOURCE_BYTES + 1))
    assert image_codec.encode_path(path) is None


def test_encode_path_rejects_non_image(tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"<html>not an image</html>")
    assert image_codec.encode_path(path) is None


def test_encode_path_is_cached(tmp_path):
    path = tmp_path / "art.png"
    path.write_bytes(_image("PNG"))
    first = image_codec.encode_path(path, edge=100)
    second = image_codec.encode_path(path, edge=100)
    assert first == second


def test_encode_path_cache_invalidated_by_mtime(tmp_path):
    path = tmp_path / "art.png"
    path.write_bytes(_image("PNG", color=(10, 10, 10)))
    first = image_codec.encode_path(path, edge=100)
    path.write_bytes(_image("PNG", color=(240, 240, 240)))
    second = image_codec.encode_path(path, edge=100)
    assert first != second


def test_encode_path_cache_can_be_disabled(tmp_path):
    path = tmp_path / "art.png"
    path.write_bytes(_image("PNG"))
    assert image_codec.encode_path(path, use_cache=False) is not None


# ------------------------------------------------------------------ 首字符头像


def test_initial_avatar_is_webp_data_uri():
    uri = image_codec.make_initial_avatar("山吹桐生")
    assert uri.startswith("data:image/webp;base64,")
    image = _decode(uri)
    assert image.size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_initial_avatar_handles_empty_name():
    uri = image_codec.make_initial_avatar("")
    assert uri.startswith("data:image/webp;base64,")


def test_initial_avatar_handles_emoji():
    uri = image_codec.make_initial_avatar("🐱喵")
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_initial_avatar_is_small():
    uri = image_codec.make_initial_avatar("山吹桐生")
    payload = len(base64.b64decode(uri.partition(",")[2]))
    assert payload < image_codec.MAX_AVATAR_BYTES
    assert payload < 32 * 1024

# ------------------------------------------------------------------ 构图探测

def test_probe_size_reads_dimensions(tmp_path):
    path = tmp_path / "wide.webp"
    path.write_bytes(_image("WEBP", size=(900, 500)))
    assert image_codec.probe_size(path) == (900, 500)


def test_probe_size_missing_file_is_none(tmp_path):
    assert image_codec.probe_size(tmp_path / "nope.webp") is None


def test_is_wide_only_for_landscape(tmp_path):
    wide = tmp_path / "wide.webp"
    wide.write_bytes(_image("WEBP", size=(900, 500)))
    tall = tmp_path / "tall.webp"
    tall.write_bytes(_image("WEBP", size=(363, 960)))
    square = tmp_path / "square.webp"
    square.write_bytes(_image("WEBP", size=(720, 720)))

    assert image_codec.is_wide_image(wide) is True
    assert image_codec.is_wide_image(tall) is False
    assert image_codec.is_wide_image(square) is False


def test_is_wide_false_for_unreadable(tmp_path):
    broken = tmp_path / "broken.webp"
    broken.write_bytes(b"not an image")
    assert image_codec.is_wide_image(broken) is False