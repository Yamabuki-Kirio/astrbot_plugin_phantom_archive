"""T2I 客户端测试：请求协议、响应校验、配置回落。

不连真实渲染服务 —— 用 httpx.MockTransport 把「服务端」搬到进程里，
这样协议字段、鉴权头、体积上限、坏图拦截都能被确定性地验证。
"""

from __future__ import annotations

import io

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("PIL")
pytest.importorskip("jinja2")

from PIL import Image  # noqa: E402

from archive import image_renderer  # noqa: E402
from archive.image_renderer import (  # noqa: E402
    ImageRenderer,
    RenderNotConfigured,
    RenderPayloadError,
    RenderSettings,
    RenderStatusError,
    RenderTransportError,
    build_payload,
    validate_image,
)


def make_image(fmt: str = "PNG", size: tuple[int, int] = (80, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (20, 30, 40)).save(buffer, format=fmt)
    return buffer.getvalue()


# ------------------------------------------------------------------ 请求协议


def test_payload_uses_tmpl_not_html():
    settings = RenderSettings(endpoint="http://example.invalid/render")
    payload = build_payload("<html></html>", settings)

    assert payload["tmpl"] == "<html></html>"
    assert "html" not in payload, "协议字段是 tmpl，写成 html 服务端会报模板缺失"
    assert payload["json"] == {}
    assert payload["tmpldata"] == {}


def test_payload_options():
    settings = RenderSettings(
        endpoint="http://example.invalid/render",
        image_format="jpeg",
        quality=77,
        scale="fhd",
        render_timeout_ms=12345,
    )
    options = build_payload("<html></html>", settings)["options"]

    assert options["full_page"] is True
    assert options["type"] == "jpeg"
    assert options["quality"] == 77
    assert options["device_scale_factor_level"] == "fhd"
    assert options["timeout"] == 12345


def test_no_hardcoded_endpoint_by_default():
    """默认配置必须是「没配地址」，不允许代码里塞默认域名。"""
    assert RenderSettings().endpoint == ""
    assert RenderSettings().configured is False


# ------------------------------------------------------------------ 配置


def test_settings_from_config_defaults():
    settings = RenderSettings.from_config(lambda key, default: default)
    assert settings.endpoint == ""
    assert settings.image_format == "png"
    assert settings.quality == 90
    assert settings.scale == "hd"
    assert settings.render_timeout_ms == 30000
    assert settings.request_timeout_ms == 45000
    assert settings.max_response_mb == 16


def test_settings_from_config_reads_values():
    config = {
        "t2i_endpoint": "http://127.0.0.1:9/render",
        "t2i_token": "secret-token",
        "t2i_image_format": "JPG",
        "t2i_quality": 80,
        "t2i_scale": "sd",
        "t2i_render_timeout_ms": 20000,
        "t2i_request_timeout_ms": 30000,
        "t2i_max_response_mb": 8,
    }
    settings = RenderSettings.from_config(lambda key, default: config.get(key, default))

    assert settings.endpoint == "http://127.0.0.1:9/render"
    assert settings.token == "secret-token"
    assert settings.image_format == "jpeg"  # 别名归一
    assert settings.quality == 80
    assert settings.scale == "sd"
    assert settings.max_response_mb == 8


@pytest.mark.parametrize(
    "overrides",
    [
        {"t2i_image_format": "webp"},
        {"t2i_quality": 0},
        {"t2i_quality": 999},
        {"t2i_scale": "8k"},
        {"t2i_render_timeout_ms": "abc"},
        {"t2i_max_response_mb": 4096},
        {"t2i_request_timeout_ms": -1},
    ],
)
def test_settings_invalid_values_fall_back(overrides):
    settings = RenderSettings.from_config(lambda key, default: overrides.get(key, default))
    assert settings.image_format == "png"
    assert 10 <= settings.quality <= 100
    assert settings.scale in image_renderer.ALLOWED_SCALES
    assert 1_000 <= settings.render_timeout_ms <= 300_000
    assert 1 <= settings.max_response_mb <= image_renderer.MAX_RESPONSE_MB_CEILING


def test_describe_never_leaks_token():
    settings = RenderSettings(endpoint="http://h.example/r", token="super-secret")
    described = settings.describe()

    assert described["token_set"] is True
    assert "super-secret" not in str(described)


def test_masked_endpoint_strips_query_and_userinfo():
    settings = RenderSettings(endpoint="http://user:pass@h.example:8080/r?token=abc")
    assert settings.masked_endpoint == "http://h.example:8080/r"


# ------------------------------------------------------------------ 响应校验


def test_validate_accepts_png():
    fmt, width, height = validate_image(make_image("PNG"), max_bytes=1 << 20)
    assert (fmt, width, height) == ("png", 80, 40)


def test_validate_accepts_jpeg():
    fmt, width, height = validate_image(make_image("JPEG"), max_bytes=1 << 20)
    assert (fmt, width, height) == ("jpeg", 80, 40)


def test_validate_rejects_empty():
    with pytest.raises(RenderPayloadError):
        validate_image(b"", max_bytes=1 << 20)


def test_validate_rejects_html_error_page():
    with pytest.raises(RenderPayloadError) as exc:
        validate_image(b"<!DOCTYPE html><html><body>500</body></html>", max_bytes=1 << 20)
    assert "HTML" in str(exc.value)


def test_validate_rejects_json_error():
    with pytest.raises(RenderPayloadError) as exc:
        validate_image(b'{"error": "bad template"}', max_bytes=1 << 20)
    assert "JSON" in str(exc.value)


def test_validate_rejects_svg():
    with pytest.raises(RenderPayloadError) as exc:
        validate_image(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', max_bytes=1 << 20)
    assert "SVG" in str(exc.value) or "PNG" in str(exc.value)


def test_validate_rejects_oversize():
    data = make_image("PNG")
    with pytest.raises(RenderPayloadError) as exc:
        validate_image(data, max_bytes=len(data) - 1)
    assert "体积" in str(exc.value)


def test_validate_rejects_truncated_png():
    data = make_image("PNG")[:40]
    with pytest.raises(RenderPayloadError):
        validate_image(data, max_bytes=1 << 20)


# ------------------------------------------------------------------ 实际请求


def _settings(**overrides) -> RenderSettings:
    base = dict(endpoint="http://t2i.invalid/render", token="tok-123", max_response_mb=1)
    base.update(overrides)
    return RenderSettings(**base)


def _renderer(handler, settings=None) -> ImageRenderer:
    return ImageRenderer(settings or _settings(), transport=httpx.MockTransport(handler))


def test_render_success_returns_image_metadata():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, content=make_image("PNG"), headers={"content-type": "image/png"})

    renderer = _renderer(handler)

    import asyncio

    image = asyncio.run(renderer.render("<html>卡片</html>"))

    assert image.image_format == "png"
    assert (image.width, image.height) == (80, 40)
    assert image.size_kb >= 1
    assert image.elapsed_ms >= 0
    assert seen["url"] == "http://t2i.invalid/render"  # 不自动补 /generate
    assert seen["auth"] == "Bearer tok-123"
    assert b'"tmpl"' in seen["body"]
    assert b'"html"' not in seen["body"]


def test_render_without_token_sends_no_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=make_image("PNG"))

    import asyncio

    asyncio.run(_renderer(handler, _settings(token="")).render("<html></html>"))
    assert seen["auth"] is None


def test_render_status_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    import asyncio

    with pytest.raises(RenderStatusError) as exc:
        asyncio.run(_renderer(handler).render("<html></html>"))
    assert "502" in str(exc.value)


def test_render_rejects_non_image_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<!DOCTYPE html><html>error</html>")

    import asyncio

    with pytest.raises(RenderPayloadError):
        asyncio.run(_renderer(handler).render("<html></html>"))


def test_render_rejects_oversize_content_length():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=make_image("PNG"),
            headers={"content-length": str(10 * 1024 * 1024)},
        )

    import asyncio

    with pytest.raises(RenderPayloadError):
        asyncio.run(_renderer(handler, _settings(max_response_mb=1)).render("<html></html>"))


def test_render_timeout_maps_to_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    import asyncio

    with pytest.raises(RenderTransportError):
        asyncio.run(_renderer(handler).render("<html></html>"))


def test_render_without_endpoint_raises_not_configured():
    import asyncio

    with pytest.raises(RenderNotConfigured):
        asyncio.run(ImageRenderer(RenderSettings()).render("<html></html>"))


def test_render_empty_html_is_rejected():
    import asyncio

    with pytest.raises(RenderPayloadError):
        asyncio.run(_renderer(lambda request: httpx.Response(200)).render("   "))
