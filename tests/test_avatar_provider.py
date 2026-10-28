"""头像获取：缓存、体积上限、格式校验、失败回退、日志不泄露地址。"""

from __future__ import annotations

import asyncio
import base64
import io
import logging

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from archive import avatar_provider, image_codec  # noqa: E402
from archive.identity import RequesterIdentity  # noqa: E402


def _avatar_bytes(fmt="PNG", size=(300, 300), color=(200, 60, 70)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _decode(uri: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(uri.partition(",")[2])))


def _settings(**kwargs) -> avatar_provider.AvatarSettings:
    base = {"url_template": "https://avatar.invalid/{user_id}.png"}
    base.update(kwargs)
    return avatar_provider.AvatarSettings(**base)


def _provider(tmp_path, handler, **kwargs) -> avatar_provider.AvatarProvider:
    return avatar_provider.AvatarProvider(
        tmp_path,
        _settings(**kwargs),
        transport=httpx.MockTransport(handler),
    )


def _resolve(provider, identity=None, event=None) -> str:
    identity = identity or RequesterIdentity("10001", "山吹桐生")
    return asyncio.run(provider.resolve(identity, event))


@pytest.fixture(autouse=True)
def _clear_codec_cache():
    image_codec.clear_cache()
    yield
    image_codec.clear_cache()


# ------------------------------------------------------------------ 成功路径


def test_downloads_and_converts_to_webp_data_uri(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_avatar_bytes())

    uri = _resolve(_provider(tmp_path, handler))
    assert uri.startswith("data:image/webp;base64,")
    assert _decode(uri).size == (image_codec.AVATAR_EDGE, image_codec.AVATAR_EDGE)
    assert seen["url"] == "https://avatar.invalid/10001.png"


def test_non_square_avatar_is_center_cropped(tmp_path):
    uri = _resolve(
        _provider(tmp_path, lambda r: httpx.Response(200, content=_avatar_bytes(size=(600, 300))))
    )
    assert _decode(uri).size == (image_codec.AVATAR_EDGE, image_codec.AVATAR_EDGE)


def test_jpeg_avatar_accepted(tmp_path):
    uri = _resolve(
        _provider(tmp_path, lambda r: httpx.Response(200, content=_avatar_bytes("JPEG")))
    )
    assert uri.startswith("data:image/webp")


def test_existing_data_uri_is_used_as_is(tmp_path):
    provider = _provider(tmp_path, lambda r: httpx.Response(200, content=b"unused"))
    who = RequesterIdentity("1", "山吹桐生", "data:image/webp;base64,AAAA")
    assert asyncio.run(provider.resolve(who)) == "data:image/webp;base64,AAAA"


# ------------------------------------------------------------------ 缓存


def test_second_call_hits_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    provider = _provider(tmp_path, handler)
    first = _resolve(provider)
    second = _resolve(provider)
    assert first == second
    assert len(calls) == 1


def test_cache_survives_new_provider(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    _resolve(_provider(tmp_path, handler))
    _resolve(_provider(tmp_path, handler))
    assert len(calls) == 1


def test_expired_cache_refetches(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    provider = _provider(tmp_path, handler, cache_hours=1)
    _resolve(provider)

    import time as time_module

    real_time = time_module.time
    monkeypatch.setattr(
        avatar_provider.time, "time", lambda: real_time() + 2 * 3600
    )
    _resolve(provider)
    assert len(calls) == 2


def test_zero_cache_hours_always_refetches(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    provider = _provider(tmp_path, handler, cache_hours=0)
    _resolve(provider)
    _resolve(provider)
    assert len(calls) == 2


# ------------------------------------------------------------------ 回退


def test_404_falls_back_to_initial_avatar(tmp_path):
    uri = _resolve(_provider(tmp_path, lambda r: httpx.Response(404)))
    assert uri.startswith("data:image/webp;base64,")
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_oversized_content_length_rejected(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            content=_avatar_bytes(),
            headers={"content-length": str(image_codec.MAX_AVATAR_BYTES + 1)},
        )

    uri = _resolve(_provider(tmp_path, handler))
    # 首字符头像也是 webp，但尺寸是 AVATAR_SIZE 而非 AVATAR_EDGE
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_html_response_rejected(tmp_path):
    uri = _resolve(
        _provider(tmp_path, lambda r: httpx.Response(200, content=b"<!DOCTYPE html><html>"))
    )
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_network_error_falls_back(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom")

    uri = _resolve(_provider(tmp_path, handler))
    assert uri.startswith("data:image/webp")


def test_no_template_and_no_platform_url_uses_initial(tmp_path):
    provider = avatar_provider.AvatarProvider(
        tmp_path,
        avatar_provider.AvatarSettings(url_template=""),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_avatar_bytes())),
    )
    uri = _resolve(provider)
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_template_without_placeholder_is_ignored():
    settings = avatar_provider.AvatarSettings.from_config(
        lambda key, default: {"qq_avatar_url_template": "https://x.invalid/a.png"}.get(key, default)
    )
    assert settings.url_template == ""
    assert settings.has_template is False


def test_empty_user_id_uses_initial(tmp_path):
    uri = _resolve(_provider(tmp_path, lambda r: httpx.Response(200, content=_avatar_bytes())),
                   RequesterIdentity("", "山吹桐生"))
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


class Event:
    """贴近 AstrBot 的事件替身：``platform_meta.name`` 是平台类型。"""

    def __init__(self, kind: str = "", avatar: str = ""):
        self.platform_meta = type("Meta", (), {"name": kind, "id": f"{kind}-1"})()
        sender = type("Sender", (), {"avatar": avatar} if avatar else {})()
        self.message_obj = type("M", (), {"sender": sender})()


def _event(kind: str = "", avatar: str = "") -> Event:
    return Event(kind, avatar)


def test_config_template_beats_platform_url(tmp_path):
    """用户手填模板优先于平台字段：配置即最终决定。"""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_avatar_bytes())

    _resolve(
        _provider(tmp_path, handler),
        event=_event("telegram", "https://platform.invalid/pic.png"),
    )
    assert seen["url"] == "https://avatar.invalid/10001.png"


# ------------------------------------------------------------------ QQ 自动


def test_qq_auto_uses_qlogo_for_onebot_numeric_id(tmp_path):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_avatar_bytes())

    uri = _resolve(_provider(tmp_path, handler, url_template=""), event=_event("aiocqhttp"))
    assert seen["url"] == (
        f"https://q1.qlogo.cn/g?b=qq&nk=10001&s=640"
    )
    assert _decode(uri).size == (image_codec.AVATAR_EDGE, image_codec.AVATAR_EDGE)


def test_qq_auto_beats_platform_field_on_onebot(tmp_path):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_avatar_bytes())

    _resolve(
        _provider(tmp_path, handler, url_template=""),
        event=_event("aiocqhttp", "https://platform.invalid/pic.png"),
    )
    assert seen["url"].startswith("https://q1.qlogo.cn/")


def test_qq_auto_skipped_when_id_is_not_numeric(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    uri = _resolve(
        _provider(tmp_path, handler, url_template=""),
        RequesterIdentity("openid-abc", "山吹桐生"),
        event=_event("aiocqhttp"),
    )
    assert calls == []
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


@pytest.mark.parametrize("kind", ["telegram", "discord", "lark", "qq_official"])
def test_qq_auto_skipped_for_non_onebot_platforms(tmp_path, kind):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    uri = _resolve(_provider(tmp_path, handler, url_template=""), event=_event(kind))
    assert calls == []
    assert _decode(uri).size == (image_codec.AVATAR_SIZE, image_codec.AVATAR_SIZE)


def test_event_avatar_used_for_non_onebot_platform(tmp_path):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_avatar_bytes())

    _resolve(
        _provider(tmp_path, handler, url_template=""),
        event=_event("telegram", "https://platform.invalid/pic.png"),
    )
    assert seen["url"] == "https://platform.invalid/pic.png"


def test_qq_number_rules():
    assert avatar_provider.is_qq_number("10001") is True
    assert avatar_provider.is_qq_number(" 123456789 ") is True
    assert avatar_provider.is_qq_number("1234") is False
    assert avatar_provider.is_qq_number("1234567890123") is False
    assert avatar_provider.is_qq_number("1000a") is False
    assert avatar_provider.is_qq_number("１０００１") is False


def test_onebot_platform_rules():
    assert avatar_provider.is_onebot_platform("aiocqhttp") is True
    assert avatar_provider.is_onebot_platform("OneBot_v11") is True
    assert avatar_provider.is_onebot_platform("telegram") is False
    assert avatar_provider.is_onebot_platform("") is False


def test_auto_qq_avatar_url_only_for_onebot_numeric():
    assert avatar_provider.auto_qq_avatar_url("aiocqhttp", "10001").endswith("nk=10001&s=640")
    assert avatar_provider.auto_qq_avatar_url("telegram", "10001") == ""
    assert avatar_provider.auto_qq_avatar_url("aiocqhttp", "abc") == ""


def test_cache_key_separates_platforms(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_avatar_bytes())

    provider = _provider(tmp_path, handler)
    _resolve(provider, event=_event("aiocqhttp"))
    _resolve(provider, event=_event("telegram"))
    assert len(calls) == 2
    assert len(list((tmp_path / avatar_provider.AVATAR_DIRNAME).glob("*.webp"))) == 2


def test_proxy_flag_passed_to_client(tmp_path, monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    provider = avatar_provider.AvatarProvider(
        tmp_path, avatar_provider.AvatarSettings(use_system_proxy=True)
    )
    provider._ensure_client()
    assert captured["trust_env"] is True


def test_proxy_flag_defaults_to_direct(tmp_path, monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    avatar_provider.AvatarProvider(tmp_path)._ensure_client()
    assert captured["trust_env"] is False


def test_proxy_flag_from_config():
    settings = avatar_provider.AvatarSettings.from_config(
        lambda key, default: {"avatar_use_system_proxy": "true"}.get(key, default)
    )
    assert settings.use_system_proxy is True


def test_logs_record_source_without_url_or_id(tmp_path, caplog):
    caplog.set_level(logging.INFO)

    def handler(request):
        raise httpx.ConnectError("boom")

    _resolve(_provider(tmp_path, handler, url_template=""), event=_event("aiocqhttp"))
    assert "qq_auto" in caplog.text
    assert "下载失败" in caplog.text
    assert "qlogo" not in caplog.text
    assert "10001" not in caplog.text


# ------------------------------------------------------------------ 安全


def test_logs_never_contain_url(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        raise httpx.ConnectError("boom")

    _resolve(_provider(tmp_path, handler))
    assert "avatar.invalid" not in caplog.text
    assert "10001" not in caplog.text


def test_settings_clamped_from_config():
    values = {
        "avatar_cache_hours": -5,
        "avatar_request_timeout": 999,
        "qq_avatar_url_template": "https://x.invalid/{user_id}",
    }
    settings = avatar_provider.AvatarSettings.from_config(
        lambda key, default: values.get(key, default)
    )
    assert settings.cache_hours == 0
    assert settings.timeout_s == avatar_provider.MAX_TIMEOUT_S
    assert settings.ttl_seconds == 0


def test_default_settings():
    settings = avatar_provider.AvatarSettings.from_config(lambda key, default: default)
    assert settings.cache_hours == avatar_provider.DEFAULT_CACHE_HOURS
    assert settings.timeout_s == avatar_provider.DEFAULT_TIMEOUT_S
    assert settings.has_template is False
